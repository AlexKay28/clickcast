"""Accumulator that turns a running pipeline into a :class:`Report`."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from clickcast.core.actions import ActionResult, BaseStep
from clickcast.feedback.advisories import no_dom_reaction
from clickcast.feedback.collector import PageStateCollector
from clickcast.feedback.graph import build_graph
from clickcast.feedback.models import (
    AccessibilityState,
    AnnotateMetadata,
    DiscoveredElement,
    ElementAccessibility,
    ErrorCode,
    Graph,
    GridMetadata,
    Media,
    PageState,
    Report,
    SkipReason,
    StepReport,
)

# Step actions a "no DOM reaction" downgrade applies to — a click-shaped
# action that lands but visibly does nothing. Matches the actions
# `feedback/advisories.py`'s tour-level advisory considers.
_REACTION_CHECKED_ACTIONS = frozenset({"click", "dblclick"})


def _package_version() -> str:
    try:
        return version("clickcast")
    except PackageNotFoundError:
        return "0.0.0+unknown"


if TYPE_CHECKING:
    from clickcast.annotate.grid import GridConfig
    from clickcast.core.session import Session
    from clickcast.discovery import AccessibleElement, Element


__all__ = ["ReportBuilder"]


_COMMON_STEP_FIELDS = {"action", "label", "dwell", "optional", "repeat"}


class ReportBuilder:
    """Stateful builder — one instance per reel run."""

    def __init__(
        self,
        *,
        url: str | None = None,
        engine: str = "chromium",
        viewport: tuple[int, int] | list[int] | None = None,
    ) -> None:
        self._url = url
        self._engine = engine
        self._viewport: list[int] = list(viewport) if viewport else [1280, 800]
        self._discovered: list[DiscoveredElement] = []
        self._steps: list[StepReport] = []
        self._warnings: list[str] = []
        self._errors: list[str] = []

        self._collector: PageStateCollector | None = None
        self._last_page_state: PageState | None = None
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._start_mono = time.monotonic()
        # #171: grid overlay params (pitch/style/color) recorded when the
        # reel was rendered with ``--grid``. ``None`` when the grid was
        # off — the sidecar then omits the ``annotate`` block entirely.
        self._grid: GridMetadata | None = None

    def attach(self, session: Session) -> None:
        """Wire the collector to the session — call once, at the start.
        Since #98 the collector uses Session's narrow event surface
        (``session.on/off``) instead of reaching through ``session.page``."""
        self._collector = PageStateCollector(session)

    def set_grid(self, grid: GridConfig) -> None:
        """Attach the grid overlay's render params to the sidecar (#171).

        The sidecar's ``annotate.grid`` block reflects the pitch, style,
        and color the reel was actually rendered with — so an agent
        parsing the reel knows the coordinate system it's measuring
        against. No-op-safe: pass a disabled :class:`GridConfig` (or
        just don't call this method) and the block stays absent.
        """
        if not grid.enabled:
            return
        # ``grid.style`` is already ``Literal["full", "ruler"]`` in GridConfig,
        # but we normalise defensively so a caller who passed a bare ``str``
        # (bypassing the type checker) still lands on a valid enum.
        style: Literal["full", "ruler"] = "ruler" if grid.style == "ruler" else "full"
        self._grid = GridMetadata(pitch=grid.pitch, style=style, color=grid.color)

    def set_discovered(
        self,
        elements: list[Element],
        accessibility: list[AccessibleElement] | None = None,
    ) -> None:
        """Record the discovery pool, optionally fused with per-element
        accessibility nodes (#196/#199).

        ``accessibility`` — when passed — must be positionally aligned with
        ``elements`` (the shape :func:`~clickcast.discovery.capture_accessibility_batch`
        already returns for a given ``elements`` list). ``None`` (the
        default) leaves every ``DiscoveredElement.accessibility`` at its
        model default (``None``) — the pre-v4 behaviour, unchanged.
        """
        if accessibility is not None and len(accessibility) != len(elements):
            raise ValueError(
                "accessibility list must be positionally aligned with elements "
                f"(got {len(accessibility)} accessibility entries for {len(elements)} elements)"
            )
        self._discovered = [
            DiscoveredElement(
                selector=e.selector,
                role=e.role,
                text=e.text,
                bbox=list(e.bbox),
                score=e.score,
                source=e.source,
                accessibility=(
                    ElementAccessibility(
                        role=a.role,
                        name=a.name,
                        state=AccessibilityState(
                            disabled=a.state.disabled,
                            checked=a.state.checked,
                            expanded=a.state.expanded,
                            pressed=a.state.pressed,
                            selected=a.state.selected,
                        ),
                        grid_cell=list(a.grid_cell) if a.grid_cell is not None else None,
                    )
                    if a is not None
                    else None
                ),
            )
            for e, a in zip(
                elements,
                accessibility if accessibility is not None else [None] * len(elements),
                strict=True,
            )
        ]

    async def record_step(
        self,
        *,
        index: int,
        step: BaseStep,
        result: ActionResult,
        frames: list[Path] | None = None,
    ) -> None:
        page_state = None
        if self._collector is not None:
            page_state = await self._collector.snapshot_and_clear()

        args = step.model_dump(exclude=_COMMON_STEP_FIELDS)

        # #227: an `optional` click/dblclick that ran clean (execute() never
        # raised) but produced no observable page reaction is exactly the
        # case `SkipReason.optional_no_reaction` names — downgrade it here
        # rather than leaving it reported as a plain "ok" step. Shares the
        # same conservative url/title comparison as the tour-level
        # `click-no-dom-reaction` stderr advisory (see `no_dom_reaction`);
        # non-optional no-reaction clicks are left to that advisory instead.
        status: Literal["ok", "failed", "skipped"] = result.status
        skip_reason = result.skip_reason
        error = result.error
        if (
            status == "ok"
            and step.optional
            and step.action in _REACTION_CHECKED_ACTIONS
            and no_dom_reaction(self._last_page_state, page_state)
        ):
            status = "skipped"
            skip_reason = "optional_no_reaction"
            error = "click produced no observable page reaction (url and title unchanged)"

        self._steps.append(
            StepReport(
                index=index,
                action=step.action,
                args=args,
                status=status,
                duration_ms=result.duration_ms,
                frames=[Path(p).name for p in (frames or [])],
                label=step.label,
                cursor_xy=list(result.cursor_xy) if result.cursor_xy else None,
                page_state=page_state,
                error=error,
                # See #151 (AI-2, AI-5): schema-v3 gate fields; the action
                # engine populates them on failed / skipped steps and leaves
                # them ``None`` on successful ones. Cast because ActionResult
                # types the fields as ``str | None`` (the enum lives in the
                # feedback layer, not the action engine).
                error_code=cast("ErrorCode | None", result.error_code),
                skip_reason=cast("SkipReason | None", skip_reason),
            )
        )
        self._last_page_state = page_state

    @property
    def steps(self) -> list[StepReport]:
        """Read-only view of the recorded step reports so far.

        Exposed so the orchestrator can feed the running step history to
        :func:`clickcast.feedback.build_advisories` at tour end without
        reaching into the private list. Returns the live list (not a copy)
        — callers must not mutate it.
        """
        return self._steps

    def add_warning(self, msg: str) -> None:
        self._warnings.append(msg)

    def add_error(self, msg: str) -> None:
        self._errors.append(msg)

    def build(self, media: Media) -> Report:
        graph: Graph | None = None
        # #107 Track C: attach the v2 graph block best-effort. A malformed
        # graph must NEVER prevent the sidecar from being written — the
        # sidecar is the primary contract; the graph is an additive.
        try:
            graph = build_graph(self._steps)
        except Exception as exc:  # pragma: no cover — defensive
            self._warnings.append(f"graph build failed: {exc!r}")

        annotate_block: AnnotateMetadata | None = None
        if self._grid is not None:
            annotate_block = AnnotateMetadata(grid=self._grid)

        report = Report(
            clickcast_version=_package_version(),
            url=self._url,
            engine=self._engine,
            viewport=self._viewport,
            started_at=self._started_at,
            duration_s=time.monotonic() - self._start_mono,
            media=media,
            discovered_elements=self._discovered,
            steps=self._steps,
            warnings=self._warnings,
            errors=self._errors,
            graph=graph,
            annotate=annotate_block,
        )
        # Detach page listeners so the collector doesn't outlive the builder.
        # Idempotent — safe to call even if we were never attached.
        if self._collector is not None:
            self._collector.detach()
        return report
