"""MCP server wrapping clickcast's session/action engine (#191, #193).

Every action tool (``goto``/``click``/.../``screenshot``) is a thin wrapper
around the matching ``Step`` model in :mod:`clickcast.core.actions` — the
handler builds the step and calls the same ``execute(step, session)``
dispatcher the CLI's ``run``/``auto`` commands use, so nothing about how an
action resolves, times out, or gets classified differs between a batch
scenario and a live MCP call. See ``docs/mcp-tool-schema.md`` for the full
contract.

v1 is single-session, single-process (see #191 "Out of scope"): one
``ClickcastSessionState`` instance backs the whole server, and
``start_session``/``close_session`` open/close the one ``Session`` it can
hold at a time.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar, cast

from mcp.server.fastmcp import FastMCP

from clickcast.annotate import AnnotateConfig, Annotator, GridConfig, ripple_stage_for
from clickcast.core.actions import (
    BaseStep,
    ClickStep,
    DblClickStep,
    GotoStep,
    HoverStep,
    PressStep,
    ScreenshotStep,
    ScrollStep,
    SelectStep,
    TypeStep,
    WaitStep,
    execute,
)
from clickcast.core.opts import BrowserOpts
from clickcast.core.session import Session, WaitArg
from clickcast.core.viewport import Viewport
from clickcast.feedback import PageState, ReportBuilder, StepDelta
from clickcast.kernel import finalize_report, transcript_media, write_sidecar
from clickcast.perception import observe as _observe
from clickcast.perception import settle as settle_session
from clickcast.perception.delta import capture as _capture_observation
from clickcast.perception.delta import diff_observations as _diff_observations
from clickcast.perception.refs import registry_for
from mcp import types as mcp_types

__all__ = ["ClickcastSessionState", "create_server", "serve_stdio"]

_log = logging.getLogger("clickcast.mcp")

_ERROR_NO_SESSION = "no active session — call start_session first"
_ERROR_SESSION_ACTIVE = "a session is already active — call close_session first"

_INSTRUCTIONS = (
    "Drive a live browser session one action at a time. Call start_session "
    "first, then goto/click/dblclick/hover/type/press/select/scroll/wait/"
    "screenshot as needed, then close_session when done. Every action tool "
    "returns an annotated PNG frame plus a JSON page_state/error_code block "
    "— gate on error_code, not on message text. observe returns a text-only, "
    "token-bounded snapshot with stable ref=eN handles."
)


@dataclass(slots=True)
class ClickcastSessionState:
    """Mutable live-session state for one MCP server process.

    One instance backs one :func:`create_server` call. ``default_browser``
    / ``default_grid`` come from the CLI's ``--engine``/``--viewport``/...
    flags (or ``Config`` defaults) and are the fallback for any
    ``start_session`` argument the caller omits — mirrors how every other
    clickcast entrypoint layers CLI flags over ``Config``.
    """

    default_browser: BrowserOpts = field(default_factory=BrowserOpts)
    default_grid: GridConfig | None = None

    session: Session | None = None
    builder: ReportBuilder | None = None
    annotator: Annotator | None = None
    grid: GridConfig | None = None
    step_index: int = 0
    #: Budget for the settle() embedded in every action's delta (#297).
    #: The explicit ``settle`` tool keeps the full 3000 ms default; an
    #: embedded verdict only needs to be good enough to tell the caller
    #: whether the page had settled, not to be maximally patient. ``0``
    #: skips the embedded settle entirely (deltas carry ``settled: null``).
    delta_settle_budget_ms: int = 1500
    _tmpdir: TemporaryDirectory[str] | None = None
    frames_dir: Path | None = None

    def reset(self) -> None:
        self.session = None
        self.builder = None
        self.annotator = None
        self.grid = None
        self.step_index = 0
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
        self._tmpdir = None
        self.frames_dir = None


def _error_result(error_code: str, message: str) -> mcp_types.CallToolResult:
    payload = {
        "ok": False,
        "status": "failed",
        "error": message,
        "error_code": error_code,
    }
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=json.dumps(payload))],
        isError=True,
    )


def _compile_redact_patterns(raw: list[str] | None) -> list[re.Pattern[str]]:
    """Compile transcript redaction patterns.

    A malformed regex raises :class:`ValueError`, which ``_safe_tool`` turns
    into the standard ``other`` error payload rather than a traceback.
    """
    if not raw:
        return []
    compiled: list[re.Pattern[str]] = []
    for source in raw:
        try:
            compiled.append(re.compile(source))
        except re.error as exc:
            raise ValueError(f"invalid redact_patterns entry {source!r}: {exc}") from exc
    return compiled


_F = TypeVar("_F", bound="Callable[..., Awaitable[mcp_types.CallToolResult]]")


def _safe_tool(fn: _F) -> _F:
    """Belt-and-suspenders: guarantee no raw traceback ever crosses the MCP
    boundary. ``execute()`` already classifies every action failure into
    :class:`~clickcast.core.actions.ActionResult`, so this only fires for
    genuinely unexpected bugs (a malformed argument combination, a frame-
    capture crash outside the try/except in :func:`_run_step`, etc).
    """

    @wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> mcp_types.CallToolResult:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover — defensive fallback
            _log.exception("unexpected error in MCP tool %s", fn.__name__)
            return _error_result("other", f"{type(exc).__name__}: {exc}")

    return cast("_F", _wrapped)


def _default_label(step: BaseStep) -> str:
    primary = (
        getattr(step, "selector", None)
        or getattr(step, "into", None)
        or getattr(step, "url", None)
        or ""
    )
    return f"{step.action}: {primary[:40]}" if primary else step.action


def _resolve_grid(
    default: GridConfig | None,
    enabled: bool | None,
    pitch: int | None,
    color: str | None,
    style: str | None,
) -> GridConfig | None:
    """Merge ``start_session`` grid args over the server's default grid.

    ``enabled=None`` means "use the default's on/off state"; an explicit
    ``True``/``False`` always wins. Any of ``pitch``/``color``/``style``
    that's set overrides the corresponding default field.
    """
    base = default or GridConfig(enabled=False)
    is_enabled = base.enabled if enabled is None else enabled
    if not is_enabled:
        return None
    return GridConfig(
        enabled=True,
        pitch=pitch if pitch is not None else base.pitch,
        color=color if color is not None else base.color,
        style=style if style is not None else base.style,  # type: ignore[arg-type]
    )


def create_server(
    *,
    name: str = "clickcast",
    default_browser: BrowserOpts | None = None,
    default_grid: GridConfig | None = None,
) -> FastMCP:
    """Build a configured :class:`FastMCP` instance.

    Kept separate from :func:`serve_stdio` so tests (and any future
    non-stdio embedding) can drive the server in-process via
    ``mcp.shared.memory.create_connected_server_and_client_session``
    instead of spawning a subprocess.
    """
    state = ClickcastSessionState(
        default_browser=default_browser or BrowserOpts(),
        default_grid=default_grid,
    )
    server = FastMCP(name, instructions=_INSTRUCTIONS)

    def _target(
        selector: str | None, ref: str | None, tool: str
    ) -> tuple[str | None, mcp_types.CallToolResult | None]:
        """Resolve an action's ``selector``/``ref`` pair to one selector.

        ``ref`` is the opaque ``eN`` handle from ``observe`` (#264). A ref
        that is unknown, or that was reported removed by a later
        observation, is a clean ``other`` error payload — never a traceback
        and never a silently-empty selector.
        """
        if selector is not None and ref is not None:
            return None, _error_result("other", f"{tool}: pass either selector or ref, not both")
        if ref is None:
            if selector is None:
                return None, _error_result("other", f"{tool}: requires selector or ref")
            return selector, None
        if state.session is None:
            return None, _error_result("other", _ERROR_NO_SESSION)
        registry = registry_for(state.session)
        if registry.is_removed(ref):
            return None, _error_result(
                "other", f"{tool}: ref {ref!r} was removed — call observe for current refs"
            )
        resolved = registry.lookup(ref)
        if resolved is None:
            return None, _error_result(
                "other", f"{tool}: unknown ref {ref!r} — call observe for current refs"
            )
        return resolved, None

    async def _run_step(
        step: BaseStep, *, full_page: bool = False, capture_delta: bool = True
    ) -> mcp_types.CallToolResult:
        """Execute one step against the live session and build its response.

        Reused by every action tool below — see module docstring. Each call
        also captures a before/after :class:`~clickcast.perception.delta.Observation`
        pair (plus a :func:`~clickcast.perception.settle.settle` verdict) and
        ships the resulting ``delta`` in the response and on the recorded
        step, so a live transcript carries the same v5 ``steps[].delta``
        block a consumer sees (#264).

        ``capture_delta=False`` is for read-only tools (``screenshot``): they
        cannot change the page, so the two observations and the settle would
        buy an always-empty delta at the cost of seconds per call (#297).
        """
        if state.session is None or state.builder is None or state.annotator is None:
            return _error_result("other", _ERROR_NO_SESSION)
        session = state.session
        builder = state.builder
        annotator = state.annotator
        frames_dir = state.frames_dir
        assert frames_dir is not None  # set alongside session in start_session

        index = state.step_index
        state.step_index += 1

        before = None
        if capture_delta:
            try:
                before = await _capture_observation(session)
            except Exception as exc:  # a crashed/blank page must not break the action
                _log.warning("delta before-capture failed for step %d: %r", index, exc)

        result = await execute(step, session, step_index=index)

        content: list[mcp_types.ContentBlock] = []
        frame_path = frames_dir / f"step-{index:04d}.png"
        captured = False
        try:
            await session.screenshot(path=frame_path, full_page=full_page)
            click_at = (
                result.cursor_xy
                if step.action in ("click", "dblclick") and result.status == "ok"
                else None
            )
            # One shared rule with the batch pipeline (#262): a live frame is
            # a single post-click frame, so it is ripple stage 1 — not the
            # fully-expanded final stage this used to pass.
            ripple_stage = ripple_stage_for(
                click_at=click_at,
                sub_index=0,
                pre_click_count=0,
                stages=annotator.config.ripple.stages,
            )
            annotator.annotate(
                frame_path,
                out_path=frame_path,
                step_index=0,
                total_steps=1,
                label=step.label or _default_label(step),
                cursor_xy=result.cursor_xy,
                click_at=click_at,
                ripple_stage=ripple_stage,
            )
            captured = True
            image_b64 = base64.b64encode(frame_path.read_bytes()).decode("ascii")
            content.append(
                mcp_types.ImageContent(type="image", data=image_b64, mimeType="image/png")
            )
        except Exception as exc:
            # A crashed/closed page can make the post-action screenshot fail
            # even when the action itself succeeded (or failed cleanly) —
            # degrade to a text-only response rather than masking the real
            # ActionResult with a frame-capture error.
            _log.warning("frame capture failed for step %d (%s): %r", index, step.action, exc)

        # Record after the screenshot so the step carries its frame path: the
        # transcript used to report `frames: []` for every MCP step while batch
        # sidecars pointed at their PNGs.
        await builder.record_step(
            index=index,
            step=step,
            result=result,
            frames=[frame_path] if captured else None,
        )
        page_state: PageState | None = builder.steps[-1].page_state

        # #264: one delta per action. Settle first so the after-observation
        # reflects the same quiescence the agent is told about; a broken page
        # degrades to `delta: null` rather than masking the ActionResult.
        # The embedded budget is deliberately smaller than the explicit
        # `settle` tool's (#297): a verdict is worth one beat, not three.
        delta: StepDelta | None = None
        if capture_delta:
            settled = None
            budget = state.delta_settle_budget_ms
            if budget > 0:
                settled = await settle_session(session, budget_ms=budget)
            try:
                after = await _capture_observation(session)
                if before is not None:
                    delta = StepDelta.model_validate(
                        _diff_observations(
                            before,
                            after,
                            console_errors=page_state.console_errors if page_state else (),
                            network_failed=page_state.network_failed if page_state else (),
                            settled=settled,
                        )
                    )
                    builder.steps[-1].delta = delta
            except Exception as exc:
                _log.warning("delta capture failed for step %d (%s): %r", index, step.action, exc)

        payload: dict[str, Any] = {
            "ok": result.ok,
            "status": result.status,
            "action": result.action,
            "selector": result.selector,
            "duration_ms": result.duration_ms,
            "cursor_xy": list(result.cursor_xy) if result.cursor_xy else None,
            "error": result.error,
            "error_code": result.error_code,
            "skip_reason": result.skip_reason,
            "page_state": page_state.model_dump(mode="json") if page_state else None,
            "delta": delta.model_dump(mode="json") if delta is not None else None,
        }
        if state.grid is not None:
            payload["grid"] = {
                "pitch": state.grid.pitch,
                "style": state.grid.style,
                "color": state.grid.color,
            }
        content.append(mcp_types.TextContent(type="text", text=json.dumps(payload)))
        return mcp_types.CallToolResult(content=content, isError=not result.ok)

    # ----------------------------------------------------------------
    # Session lifecycle
    # ----------------------------------------------------------------

    @server.tool()
    @_safe_tool
    async def start_session(
        engine: str | None = None,
        viewport: str | None = None,
        device: str | None = None,
        headful: bool | None = None,
        lang: str | None = None,
        dark: bool | None = None,
        grid: bool | None = None,
        grid_pitch: int | None = None,
        grid_color: str | None = None,
        grid_style: str | None = None,
        settle_budget_ms: int | None = None,
    ) -> mcp_types.CallToolResult:
        """Open the one live browser session this server process holds.

        Args default to the server's ``--engine``/``--viewport``/... CLI
        flags (or ``Config`` defaults) when omitted. Errors if a session is
        already open — call ``close_session`` first.
        """
        if state.session is not None:
            return _error_result("other", _ERROR_SESSION_ACTIVE)
        if settle_budget_ms is not None and not 0 <= settle_budget_ms <= 30_000:
            return _error_result("other", "settle_budget_ms must be between 0 and 30000")

        base = state.default_browser
        opts = BrowserOpts(
            engine=engine or base.engine,
            viewport=Viewport.parse(viewport) if viewport else base.viewport,
            device=device if device is not None else base.device,
            headful=base.headful if headful is None else headful,
            lang=lang if lang is not None else base.lang,
            dark=base.dark if dark is None else dark,
        )
        grid_cfg = _resolve_grid(state.default_grid, grid, grid_pitch, grid_color, grid_style)
        if settle_budget_ms is not None:
            state.delta_settle_budget_ms = settle_budget_ms

        try:
            session = await Session(**opts.to_session_kwargs()).__aenter__()
        except Exception as exc:
            return _error_result("other", f"failed to start session: {type(exc).__name__}: {exc}")

        builder = ReportBuilder(engine=opts.engine, viewport=list(opts.viewport.as_tuple()))
        builder.attach(session)
        if grid_cfg is not None:
            builder.set_grid(grid_cfg)

        state.session = session
        state.builder = builder
        state.annotator = Annotator(
            AnnotateConfig(grid=grid_cfg, progress=False, actions_panel=False)
        )
        state.grid = grid_cfg
        state.step_index = 0
        state._tmpdir = TemporaryDirectory(prefix="clickcast-mcp-")
        state.frames_dir = Path(state._tmpdir.name)

        payload = {
            "ok": True,
            "engine": opts.engine,
            "viewport": list(opts.viewport.as_tuple()),
            "headful": opts.headful,
        }
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=json.dumps(payload))]
        )

    @server.tool()
    @_safe_tool
    async def close_session(
        save_transcript: str | None = None,
        redact_patterns: list[str] | None = None,
        strip_query_strings: bool = False,
        with_feedback: bool = False,
    ) -> mcp_types.CallToolResult:
        """Close the live session. Optionally flush the accumulated
        sidecar-shaped transcript (see ``docs/mcp-tool-schema.md``) to
        ``save_transcript`` — same JSON shape a batch ``run``/``auto``
        sidecar has, with a zeroed ``media`` block (no reel is encoded from a
        live session).

        ``redact_patterns`` / ``strip_query_strings`` / ``with_feedback``
        behave exactly as they do for ``clickcast run``: an agent that drove
        an authenticated URL can scrub tokens before the transcript hits disk.
        """
        if state.session is None:
            return _error_result("other", _ERROR_NO_SESSION)

        session = state.session
        builder = state.builder
        saved_path: str | None = None
        try:
            if builder is not None and save_transcript:
                report = finalize_report(builder, transcript_media(state.step_index))
                written = write_sidecar(
                    save_transcript,
                    report,
                    target=save_transcript,
                    redact_patterns=_compile_redact_patterns(redact_patterns),
                    strip_query_strings=strip_query_strings,
                    with_feedback=with_feedback,
                )
                saved_path = str(written) if written is not None else None
        finally:
            await session.close()
            state.reset()

        payload = {"ok": True, "closed": True, "transcript_path": saved_path}
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=json.dumps(payload))]
        )

    # ----------------------------------------------------------------
    # Action tools — one per core/actions.py Step (see docs/mcp-tool-schema.md)
    # ----------------------------------------------------------------

    @server.tool()
    @_safe_tool
    async def goto(
        url: str,
        wait: WaitArg | None = "networkidle",
        retries: int = 0,
        label: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Navigate the active session to ``url``."""
        return await _run_step(GotoStep(url=url, wait=wait, retries=retries, label=label))

    @server.tool()
    @_safe_tool
    async def click(
        selector: str | None = None,
        wait: WaitArg | None = None,
        timeout_ms: int | None = None,
        label: str | None = None,
        ref: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Click the first element matching ``selector`` (or the ``ref`` from
        ``observe``).

        Pass ``wait`` (a load state like "networkidle", a selector, or a
        number of seconds) when the click triggers client-side (SPA)
        navigation — the response otherwise reflects the page mid-transition
        rather than settled, since a route change with no full page load
        gives no other signal that it's still in flight (#226).
        """
        target, error = _target(selector, ref, "click")
        if error is not None:
            return error
        assert target is not None
        return await _run_step(
            ClickStep(selector=target, wait=wait, timeout_ms=timeout_ms, label=label)
        )

    @server.tool()
    @_safe_tool
    async def dblclick(
        selector: str | None = None,
        wait: WaitArg | None = None,
        timeout_ms: int | None = None,
        label: str | None = None,
        ref: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Double-click the first element matching ``selector`` (or ``ref``).
        See ``click``'s ``wait`` for triggering client-side navigation."""
        target, error = _target(selector, ref, "dblclick")
        if error is not None:
            return error
        assert target is not None
        return await _run_step(
            DblClickStep(selector=target, wait=wait, timeout_ms=timeout_ms, label=label)
        )

    @server.tool()
    @_safe_tool
    async def hover(
        selector: str | None = None,
        timeout_ms: int | None = None,
        label: str | None = None,
        ref: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Hover the first element matching ``selector`` (or ``ref``)."""
        target, error = _target(selector, ref, "hover")
        if error is not None:
            return error
        assert target is not None
        return await _run_step(HoverStep(selector=target, timeout_ms=timeout_ms, label=label))

    @server.tool(name="type")
    @_safe_tool
    async def type_text(
        text: str,
        into: str | None = None,
        delay: float = 0.0,
        timeout_ms: int | None = None,
        label: str | None = None,
        ref: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Type ``text`` into the first element matching ``into`` (or ``ref``)."""
        target, error = _target(into, ref, "type")
        if error is not None:
            return error
        assert target is not None
        return await _run_step(
            TypeStep(into=target, text=text, delay=delay, timeout_ms=timeout_ms, label=label)
        )

    @server.tool()
    @_safe_tool
    async def press(
        key: str,
        selector: str | None = None,
        timeout_ms: int | None = None,
        label: str | None = None,
        ref: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Press ``key``. Page-level keyboard when neither ``selector`` nor
        ``ref`` is given."""
        target, error = _target(selector, ref, "press") if ref is not None else (selector, None)
        if error is not None:
            return error
        return await _run_step(
            PressStep(key=key, selector=target, timeout_ms=timeout_ms, label=label)
        )

    @server.tool()
    @_safe_tool
    async def select(
        value: str | list[str],
        into: str | None = None,
        timeout_ms: int | None = None,
        label: str | None = None,
        ref: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Select ``value`` on the ``<select>`` matching ``into`` (or ``ref``)."""
        target, error = _target(into, ref, "select")
        if error is not None:
            return error
        assert target is not None
        return await _run_step(
            SelectStep(into=target, value=value, timeout_ms=timeout_ms, label=label)
        )

    @server.tool()
    @_safe_tool
    async def scroll(
        to: str | None = None,
        by: int | None = None,
        selector: str | None = None,
        dx: int = 0,
        label: str | None = None,
        ref: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Scroll ``to`` a selector into view (or the element behind ``ref``),
        or ``by`` pixels (window, or ``selector``'s container when given)."""
        target, error = _target(selector, ref, "scroll") if ref is not None else (selector, None)
        if error is not None:
            return error
        return await _run_step(ScrollStep(to=to, by=by, selector=target, dx=dx, label=label))

    @server.tool()
    @_safe_tool
    async def wait(
        wait: WaitArg,
        label: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Wait for a load state, a selector, or a number of seconds."""
        return await _run_step(WaitStep(wait=wait, label=label))

    @server.tool()
    @_safe_tool
    async def screenshot(
        full_page: bool = False,
        label: str | None = None,
    ) -> mcp_types.CallToolResult:
        """Capture the current page without performing any action.

        No ``delta`` is computed: a screenshot cannot change the page, so the
        observations and settle would be pure latency (#297).
        """
        return await _run_step(
            ScreenshotStep(full_page=full_page, label=label),
            full_page=full_page,
            capture_delta=False,
        )

    @server.tool()
    @_safe_tool
    async def settle(budget_ms: int | None = None) -> mcp_types.CallToolResult:
        """Wait until the page has stopped changing.

        Polls three independent signals (in-flight network requests, DOM
        mutations, compositor activity) with their own budgeted quiet
        windows. Returns ``{"ok", "ms", "signals", "timed_out"}``; a signal
        that exhausts its budget is named in ``timed_out`` instead of raising,
        and the call itself is a successful observation either way. Pass
        ``budget_ms`` for a hard ceiling on the whole call (default 3000).
        """
        if state.session is None:
            return _error_result("other", _ERROR_NO_SESSION)
        result = await settle_session(state.session, budget_ms=budget_ms)
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=json.dumps(result.to_dict()))]
        )

    @server.tool()
    @_safe_tool
    async def observe(token_budget: int = 800) -> mcp_types.CallToolResult:
        """Return a compact, token-bounded snapshot of the current page.

        Text only — no image. Each node carries a stable opaque ``ref``
        (``eN``) for the live session, plus role/name/bbox/disabled state.
        Refs stay identical across repeated calls, and refs whose elements
        disappeared are reported in the top-level ``removed`` list. See
        ``docs/mcp-tool-schema.md`` for the full ``observe/1`` contract.
        """
        if state.session is None:
            return _error_result("other", _ERROR_NO_SESSION)
        payload = await _observe(state.session, token_budget=token_budget)
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=json.dumps(payload))]
        )

    return server


def serve_stdio(
    *,
    default_browser: BrowserOpts | None = None,
    default_grid: GridConfig | None = None,
) -> None:
    """Blocking entrypoint: run the clickcast MCP server on stdio.

    ``FastMCP.run()`` manages its own event loop (``anyio.run`` under the
    hood) — callers must NOT wrap this in ``asyncio.run`` themselves.
    """
    server = create_server(default_browser=default_browser, default_grid=default_grid)
    server.run(transport="stdio")
