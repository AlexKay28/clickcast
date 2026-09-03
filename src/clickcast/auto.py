"""Auto-tour engine — shared between the CLI (`clickcast auto`) and the demo
generator script (`scripts/generate_demo.py`).

Both call `run_tour(AutoConfig(...))`. Bug fixes and features land here once,
not twice.

Public API:

- :class:`AutoConfig` — typed inputs for a tour.
- :func:`run_tour` — async orchestrator; drives BFS/DFS + click loop, applies
  overlays, encodes reel + sidecar.
- :func:`explore_page` — per-page loop (goto → discover → click x N → scroll).
  Exposed for tests + advanced callers.

Design notes:

- All timing / policy constants live at module scope, named. Every value has
  a comment tying it to the PR that tuned it — future changes stay grounded.
- The engine returns None and prints the summary via `typer.echo`. If you
  need programmatic access to results (frame count, wall time, visited URLs),
  either read the sidecar or add a return-value refactor as a follow-up.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer

from clickcast.annotate import (
    AnnotateConfig,
    CardStyle,
    GridConfig,
    StepAnnotation,
    SummaryStats,
    annotate_frames_dir,
    apply_zoom_on_click,
    interpolate_cursor_motion,
    render_summary_card,
    render_title_card,
)
from clickcast.capture import Recorder
from clickcast.core.actions import ClickStep, GotoStep, ScrollStep, execute
from clickcast.core.session import Session
from clickcast.discovery import Element, capture_accessibility_batch, discover
from clickcast.discovery.urlutil import is_same_origin, normalize_url
from clickcast.encode import encode
from clickcast.feedback import Media, ReportBuilder, StepReport, build_advisories
from clickcast.feedback import write as write_report

__all__ = ["AutoConfig", "explore_page", "run_tour"]

log = logging.getLogger("clickcast.auto")


# ---------------------------------------------------------------------------
# Tuned constants — every value here has a story. Change with care.
# ---------------------------------------------------------------------------

# `page.go_back(wait_until="domcontentloaded", timeout=_GOTO_BACK_TIMEOUT_MS)`.
# Playwright's default is 30s; we cap much lower because HMR/WebSocket sites
# never satisfy networkidle. See PR #58.
_GOTO_BACK_TIMEOUT_MS = 5000

# `page.mouse.wheel(0, _SCROLL_DISTANCE_PX)` — one screenful for 1280x800.
_SCROLL_DISTANCE_PX = 600

# Between clicks: Playwright needs a moment for the DOM to settle before we
# ask for the next element's bounding box. See PR #56.
_INTER_CLICK_WAIT_S = 0.3

# Discovery pool floor. When click_budget is small (e.g. 1-2), we still want
# a generous candidate set so nav-link options aren't starved. See #62.
_MIN_DISCOVERY_POOL = 20

# A page whose discovered elements all fail (post-hydration DOM drift is the
# usual culprit) used to burn the full pool trying every one — 20+ timeouts
# in a row. Bail after N consecutive failures. See PR #61.
_MAX_CONSECUTIVE_FAILURES = 3


def _discovery_limit(click_budget: int) -> int:
    return max(click_budget * 2, _MIN_DISCOVERY_POOL)


# ---------------------------------------------------------------------------
# Typed inputs
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AutoConfig:
    """All the knobs an auto tour cares about. Callers construct one and
    pass it to :func:`run_tour`.

    Fields group into:

    - **Target** — `url`, `out`.
    - **Traversal** — `max_steps`, `max_pages`, `max_duration`, `traversal`,
      `seed_urls`.
    - **Per-action tuning** — `click_timeout_ms`, `dwell`, `initial_wait`.
    - **Session** — `session_kwargs` (dict passed to :class:`Session`; contains
      engine/viewport/device/headful/lang/dark/slowmo).
    - **Output** — `fps`, `format`, `quality`, `loop`, `no_sidecar`.
    """

    url: str
    out: str
    max_steps: int
    max_pages: int
    max_duration: float
    click_timeout_ms: int
    dwell: float
    initial_wait: float
    session_kwargs: dict[str, Any]
    fps: int
    quality: int
    loop: int
    no_sidecar: bool
    traversal: str = "dfs"
    seed_urls: list[str] = field(default_factory=list)
    format: str | None = None
    # Zoom-on-click: crop-and-scale post-click sub-frames around the click
    # point. `None` disables. See #74 (Shape A).
    zoom_on_click_factor: float | None = None
    zoom_frames_after_click: int = 3  # default matches AnnotateConfig.ripple.stages
    # Annotate config threaded into both interpolation (reads cursor_style)
    # and annotate_frames_dir. `None` uses defaults. See #75.
    annotate: AnnotateConfig = field(default_factory=AnnotateConfig)
    # Attach the machine-discoverable feedback pointer block to the sidecar.
    # Opt-in — nothing goes on the sidecar unless the caller asks for it. See #40.
    with_feedback: bool = False
    # Sidecar redaction (#110). ``redact_patterns`` blot out matched substrings
    # with «redacted» across every string in the payload. ``strip_query_strings``
    # drops the query string from URL fields entirely. Both no-op when unset.
    redact_patterns: list[re.Pattern[str]] = field(default_factory=list)
    strip_query_strings: bool = False
    # Machine-readable summary line for JSONL-friendly downstream parsers.
    # See #151 (AI-4). Off by default; when True, ``run_tour`` prints one
    # JSON object on its own line to stdout right after the prose summary
    # ("event": "tour_complete", with frames/duration/clicks/etc.).
    emit_events: bool = False
    # Human-observable demo mode (#129). Each flag stands alone; the CLI's
    # ``--for-humans`` composite flag flips several of them at once.
    #   ``target_highlight`` — resolve each click's bbox pre-action, hold
    #   the frame for ``pre_click_highlight_frames`` extra copies, and draw
    #   a pulsing ring on those pre-click frames via the annotator. Also
    #   requires ``annotate.target_highlight=True`` (which the CLI flips
    #   in tandem — direct callers construct both explicitly).
    #   ``title_card`` / ``summary_card`` — prepend N frames of a title
    #   card / append N frames of a summary card to the reel. Both are
    #   inserted into ``frames.json`` after the annotator pass so the
    #   encoder picks them up transparently.
    target_highlight: bool = False
    pre_click_highlight_frames: int = 4
    title_card: bool = False
    title_card_text: str | None = None
    title_card_frames: int = 12
    summary_card: bool = False
    summary_card_frames: int = 16
    summary_card_watermark: str = ""
    card_style: CardStyle = field(default_factory=CardStyle)


# ---------------------------------------------------------------------------
# Per-page loop
# ---------------------------------------------------------------------------


async def _ensure_discovered(
    *,
    sess: Session,
    url: str,
    click_budget: int,
    cache: dict[str, list[Element]],
) -> list[Element]:
    """Return the discovered elements for ``url``, hitting ``cache`` first.

    See #151 (PERF-1). Discovery is the only per-page work whose cost
    scales with the pool size (``limit=_discovery_limit(budget)``); on a
    slow site each fresh ``discover()`` re-walks the DOM and re-scores
    every candidate. Cache keyed by page URL so a re-entry to the same
    page (e.g. a click-retry-loop re-fetch, or a future orchestrator that
    revisits a URL for a second pass) reuses the pool.

    Invalidation is trivial: the cache is passed in by scope and lives
    only as long as the caller wants it to. :func:`explore_page` creates
    a fresh one per page iteration, so a genuine navigation always sees
    a cold cache; the URL key guards against the same cache ever
    returning stale results for a different page.
    """
    hit = cache.get(url)
    if hit is not None:
        return hit
    elements = await discover(sess, limit=_discovery_limit(click_budget))
    cache[url] = elements
    return elements


async def _goto_and_discover(
    *,
    sess: Session,
    rec: Recorder,
    builder: ReportBuilder | None,
    url: str,
    dwell: float,
    initial_wait: float,
    click_budget: int,
    step_index: int,
    step_annotations: dict[int, StepAnnotation],
    page_label: str,
    discovery_cache: dict[str, list[Element]] | None = None,
    grid: GridConfig | None = None,
) -> tuple[int, bool, list[Element]]:
    """Goto ``url``, record the open frame, then discover clickable elements.

    Returns ``(next_step_index, ok, elements)`` — ``ok`` is False when the
    goto itself failed (caller should bail on the page); ``elements`` is
    the empty list in that case. Callers advance ``step_index`` past both
    the goto step and any subsequent per-click work.

    ``discovery_cache`` (see #151 PERF-1) is an optional page-scope cache
    keyed by URL. When provided, discovery goes through
    :func:`_ensure_discovered` so a re-entry to the same URL reuses the
    already-computed element pool instead of paying the full walk again.
    Defaults to ``None`` for direct callers / tests — behaviour is
    identical to the pre-cache path (a fresh ``discover()`` per call).

    Behaviour preserved from the pre-split ``explore_page``:

    - Same stderr line (``  skipped {url}: {error}``) on goto failure.
    - Same hydration hold via ``initial_wait`` before frame capture.
    - Same ``builder.record_step`` + ``builder.set_discovered`` calls in
      the same order (discovered pool is pinned only on the FIRST page,
      i.e. when the caller passes ``step_index == 0`` and the goto
      succeeds — matching the shipped ``if step_index == 1`` guard after
      the increment).

    ``grid`` (#196/#198): when the tour is running with an enabled
    :class:`~clickcast.annotate.grid.GridConfig`, the elements pinned to
    ``builder.set_discovered`` also carry their accessibility node +
    computed grid cell (via
    :func:`~clickcast.discovery.capture_accessibility_batch`). ``None``
    (the default — no grid) still captures accessibility, just without a
    grid cell, so ``discovered_elements[].accessibility.role/name/state``
    populate on every sidecar, grid or not.
    """
    goto = GotoStep(url=url, wait="networkidle", dwell=dwell)
    await rec.pre_action(sess)
    result = await execute(goto, sess, step_index=step_index)
    if not result.ok:
        typer.secho(f"  skipped {url}: {result.error}", fg=typer.colors.YELLOW, err=True)
        log.warning("%s · skipped: %s", page_label, result.error)
        return step_index, False, []
    if initial_wait > 0:
        log.debug("%s · held %.1fs after networkidle for hydration", page_label, initial_wait)
        await sess.wait(initial_wait)
    frames_goto = await rec.post_action(sess, result, goto)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · open")
    if builder:
        await builder.record_step(index=step_index, step=goto, result=result, frames=frames_goto)
    step_index += 1

    if discovery_cache is None:
        elements = await discover(sess, limit=_discovery_limit(click_budget))
    else:
        elements = await _ensure_discovered(
            sess=sess, url=url, click_budget=click_budget, cache=discovery_cache
        )
    log.info(
        "%s · discovered %d elements, click budget: %d", page_label, len(elements), click_budget
    )
    if builder and step_index == 1:
        pinned = elements[:click_budget]
        # #196/#197/#198: best-effort accessibility capture for the exact
        # pool the sidecar pins. Never fails the tour — any per-element
        # Playwright error already degrades to a null role/name/state
        # inside capture_accessibility_batch; a batch-level failure (e.g.
        # the page navigated away between discover() and here) is caught
        # here so a flaky a11y pass never costs the whole sidecar.
        accessibility = None
        try:
            accessibility = await capture_accessibility_batch(sess, pinned, grid=grid)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("%s · accessibility capture failed: %r", page_label, exc)
        builder.set_discovered(pinned, accessibility)
    return step_index, True, elements


async def _click_loop(
    *,
    sess: Session,
    rec: Recorder,
    builder: ReportBuilder | None,
    elements: list[Element],
    dwell: float,
    click_budget: int,
    click_timeout_ms: int,
    deadline_monotonic: float | None,
    step_index: int,
    step_annotations: dict[int, StepAnnotation],
    page_label: str,
    target_highlight: bool = False,
    pre_click_highlight_frames: int = 0,
    discovery_cache: dict[str, list[Element]] | None = None,
) -> tuple[int, int, list[str]]:
    """Click up to ``click_budget`` of the discovered ``elements``, restoring
    the page after every same-origin nav so subsequent clicks still land.

    Returns ``(next_step_index, clicks_used, discovered_urls)``. All
    click-retry policy (consecutive-failure bail, deadline early-exit,
    cross-origin bail, ``go_back`` restore) lives here — the orchestrator
    just threads state in and out.

    ``discovery_cache`` (see #151 PERF-1) is the page-scope memo dict
    threaded from :func:`explore_page`. The loop iterates the passed
    ``elements`` list directly (already the cached pool for
    ``sess.page.url`` at entry), so a timing-out click never triggers a
    fresh ``discover()``; if a future refactor lifts re-discovery into
    this loop, it should route through :func:`_ensure_discovered` with
    this cache so same-URL re-entries stay O(1).

    Behaviour preserved from the pre-split ``explore_page``:

    - Same ``ClickStep`` construction (optional=True, label from element
      text or role, timeout_ms passthrough).
    - Same target-highlight bbox resolution + pre-action pad ordering.
    - Same ``builder.record_step`` call order relative to the early-exit
      break (consecutive-failures bail records the failing step BEFORE
      incrementing ``step_index`` and breaking, matching the shipped
      order so sidecar indices stay stable).
    - Same nav-detection: URL delta appends to ``discovered_urls``;
      cross-origin breaks; same-origin triggers ``go_back`` with the
      shipped timeout, and a landing-URL mismatch stops the page.
    """
    del discovery_cache  # accepted for orchestrator symmetry; unused in-loop today
    discovered_urls: list[str] = []
    clicked = 0
    consecutive_failures = 0
    for attempt, element in enumerate(elements, start=1):
        if clicked >= click_budget:
            break
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            log.warning("%s · tour deadline reached during clicks → stopping page", page_label)
            break
        step = ClickStep(
            selector=element.selector,
            dwell=dwell,
            optional=True,
            label=element.text[:60] or element.role,
            timeout_ms=click_timeout_ms,
        )
        url_before = sess.page.url
        log.info(
            "%s · attempt %d (%d/%d clicked) · %s:%s",
            page_label,
            attempt,
            clicked,
            click_budget,
            element.role,
            (element.text[:40] or "").strip() or element.selector,
        )
        await rec.pre_action(sess)
        # Pre-click target-highlight: resolve bbox now (before the click
        # potentially navigates away), then pad the pre-click frame N times
        # so the highlight ring gets hold time. Bbox resolution is best-
        # effort — a missing/hidden target just means no ring. See #129 A.
        target_bbox: tuple[int, int, int, int] | None = None
        if target_highlight:
            try:
                target_bbox = await sess.bbox(element.selector)
            except Exception as exc:
                # Best-effort bbox lookup — post-hydration DOM drift, cross-
                # origin frames, timeout, or "0 elements" all mean "no ring
                # this step". Downgrade to a debug log rather than surfacing
                # as an error — the click itself will still be attempted.
                log.debug(
                    "%s · target bbox lookup failed for %s: %s",
                    page_label,
                    element.selector,
                    exc,
                )
            if pre_click_highlight_frames > 0:
                await rec.pre_action_pad(pre_click_highlight_frames)
        r = await execute(step, sess, step_index=step_index)
        frames_step = await rec.post_action(sess, r, step)
        step_annotations[step_index] = StepAnnotation(
            label=f"{page_label} · click · {step.label}" if step.label else f"{page_label} · click",
            click_at=r.cursor_xy if r.status == "ok" else None,
            target_bbox=target_bbox if r.status == "ok" else None,
        )
        if r.status == "ok":
            clicked += 1
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            log.warning(
                "%s · attempt %d failed (%d/%d in a row): %s",
                page_label,
                attempt,
                consecutive_failures,
                _MAX_CONSECUTIVE_FAILURES,
                r.error,
            )
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                log.warning(
                    "%s · %d consecutive click failures → stopping page early",
                    page_label,
                    consecutive_failures,
                )
                if builder:
                    await builder.record_step(
                        index=step_index, step=step, result=r, frames=frames_step
                    )
                step_index += 1
                break
        if builder:
            await builder.record_step(index=step_index, step=step, result=r, frames=frames_step)
        step_index += 1

        # Post-click: did we navigate? Note the destination and try to restore
        # the page so we can keep clicking the remaining discovered elements.
        # Same-origin nav: page.go_back() and continue. Cross-origin nav:
        # bail (we shouldn't drive further on someone else's site).
        url_after = sess.page.url
        if url_after != url_before:
            discovered_urls.append(url_after)
            if not is_same_origin(url_after, url_before):
                log.info("%s · nav to cross-origin %s → bailing", page_label, url_after)
                break
            log.info("%s · nav to %s → going back", page_label, url_after)
            back_started = time.monotonic()
            try:
                await sess.page.go_back(
                    wait_until="domcontentloaded", timeout=_GOTO_BACK_TIMEOUT_MS
                )
            except Exception as e:
                log.warning("%s · go_back failed (%s) → stopping page", page_label, e)
                break
            if sess.page.url != url_before:
                log.warning(
                    "%s · go_back landed at %s (expected %s) → stopping page",
                    page_label,
                    sess.page.url,
                    url_before,
                )
                break
            log.debug("%s · go_back OK in %.2fs", page_label, time.monotonic() - back_started)
        await sess.wait(_INTER_CLICK_WAIT_S)

    return step_index, clicked, discovered_urls


async def explore_page(
    *,
    sess: Session,
    rec: Recorder,
    builder: ReportBuilder | None,
    url: str,
    dwell: float,
    initial_wait: float,
    click_budget: int,
    click_timeout_ms: int,
    deadline_monotonic: float | None,
    step_index: int,
    step_annotations: dict[int, StepAnnotation],
    page_label: str,
    target_highlight: bool = False,
    pre_click_highlight_frames: int = 0,
    grid: GridConfig | None = None,
) -> tuple[int, int, list[str]]:
    """Goto ``url``, discover, click up to ``click_budget`` elements, scroll.

    Thin orchestrator: delegates goto+discover to :func:`_goto_and_discover`,
    click-retry policy to :func:`_click_loop`, and finishes with the shipped
    per-page scroll step so subsequent pages capture below-the-fold content.

    Returns ``(next_step_index, clicks_used, discovered_urls)``.

    A page-scope ``discovery_cache`` (see #151 PERF-1) is created here and
    threaded through both helpers so a click-retry-loop re-entry to the
    same URL reuses the discovered element pool instead of paying the
    full DOM walk again. Cache lives only for this call — the natural
    invalidation boundary is between page iterations in :func:`run_tour`,
    which starts a fresh ``explore_page`` (and therefore a cold cache)
    for every new URL.
    """
    page_started = time.monotonic()
    log.info("%s → open %s", page_label, url)

    discovery_cache: dict[str, list[Element]] = {}
    step_index, ok, elements = await _goto_and_discover(
        sess=sess,
        rec=rec,
        builder=builder,
        url=url,
        dwell=dwell,
        initial_wait=initial_wait,
        click_budget=click_budget,
        step_index=step_index,
        step_annotations=step_annotations,
        page_label=page_label,
        discovery_cache=discovery_cache,
        grid=grid,
    )
    if not ok:
        return step_index, 0, []

    step_index, clicked, discovered_urls = await _click_loop(
        sess=sess,
        rec=rec,
        builder=builder,
        elements=elements,
        dwell=dwell,
        click_budget=click_budget,
        click_timeout_ms=click_timeout_ms,
        deadline_monotonic=deadline_monotonic,
        step_index=step_index,
        step_annotations=step_annotations,
        page_label=page_label,
        target_highlight=target_highlight,
        pre_click_highlight_frames=pre_click_highlight_frames,
        discovery_cache=discovery_cache,
    )
    step_index = await _scroll_page(
        sess=sess,
        rec=rec,
        builder=builder,
        dwell=dwell,
        step_index=step_index,
        step_annotations=step_annotations,
        page_label=page_label,
    )
    log.info(
        "%s · done in %.1fs (%d clicks used, %d nav candidates)",
        page_label,
        time.monotonic() - page_started,
        clicked,
        len(discovered_urls),
    )
    return step_index, clicked, discovered_urls


async def _scroll_page(
    *,
    sess: Session,
    rec: Recorder,
    builder: ReportBuilder | None,
    dwell: float,
    step_index: int,
    step_annotations: dict[int, StepAnnotation],
    page_label: str,
) -> int:
    """Record one per-page scroll so subsequent pages start below the fold.

    Extracted for line-budget reasons (keeps :func:`explore_page` under 50
    lines). Behaviour is identical to the pre-split tail of ``explore_page``:
    same distance constant, same annotation label, same builder call site.
    """
    scroll = ScrollStep(by=_SCROLL_DISTANCE_PX, dwell=dwell)
    log.info("%s · scroll +%dpx", page_label, _SCROLL_DISTANCE_PX)
    await rec.pre_action(sess)
    r = await execute(scroll, sess, step_index=step_index)
    frames_scroll = await rec.post_action(sess, r, scroll)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · scroll")
    if builder:
        await builder.record_step(index=step_index, step=scroll, result=r, frames=frames_scroll)
    return step_index + 1


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_tour(cfg: AutoConfig) -> None:
    """Run a full auto tour. See :class:`AutoConfig` for inputs.

    Raises :class:`typer.Exit` on validation failures (matching prior CLI
    behavior). Prints a summary via :mod:`typer.echo` on completion.
    """
    if cfg.max_pages < 1:
        _die("--max-pages must be >= 1")
    if cfg.max_duration <= 0:
        _die("--max-duration must be > 0")

    seeded = bool(cfg.seed_urls)
    tour_started = time.monotonic()
    deadline = tour_started + cfg.max_duration
    log.info(
        "starting auto tour: url=%s max_pages=%d max_steps=%d max_duration=%.1fs "
        "click_timeout=%dms traversal=%s seeded=%s dwell=%.2fs fps=%d",
        cfg.url,
        cfg.max_pages,
        cfg.max_steps,
        cfg.max_duration,
        cfg.click_timeout_ms,
        cfg.traversal,
        seeded,
        cfg.dwell,
        cfg.fps,
    )

    async with Session(**cfg.session_kwargs) as sess:
        builder: ReportBuilder | None = None
        if not cfg.no_sidecar:
            builder = ReportBuilder(
                url=cfg.url,
                engine=cfg.session_kwargs.get("engine", "chromium"),
                viewport=cfg.session_kwargs.get("viewport"),
            )
            builder.attach(sess)
            # #171: surface the grid overlay's render params on the sidecar
            # so agents parsing the reel know the coordinate system it was
            # rendered with. No-op when the grid is disabled.
            if cfg.annotate.grid is not None:
                builder.set_grid(cfg.annotate.grid)

        with Recorder(fps=cfg.fps, default_dwell=cfg.dwell) as rec:
            step_annotations: dict[int, StepAnnotation] = {}
            step_index = 0
            visited: set[str] = set()
            initial_queue = [cfg.url, *cfg.seed_urls]
            queue: deque[str] = deque(initial_queue)
            pages_visited = 0
            clicks_remaining = cfg.max_steps

            # Traversal policy: DFS pops LIFO (most recently discovered first —
            # coherent narrative). BFS pops FIFO (site-map coverage). Seeded
            # tours always FIFO so seeds run in the order given.
            pop_next = queue.popleft if (seeded or cfg.traversal == "bfs") else queue.pop
            # Seeded tours honor the caller's URL commitment even past click
            # budget exhaustion — remaining seeds get goto + scroll only.
            while queue and pages_visited < cfg.max_pages:
                if not seeded and clicks_remaining <= 0:
                    break
                if time.monotonic() >= deadline:
                    log.warning(
                        "max-duration %.0fs reached before page %d/%d → stopping tour",
                        cfg.max_duration,
                        pages_visited + 1,
                        cfg.max_pages,
                    )
                    break
                current = pop_next()
                key = normalize_url(current)
                if key in visited:
                    log.debug("skipping already-visited %s", current)
                    continue
                visited.add(key)
                pages_visited += 1
                page_label = f"page {pages_visited}/{cfg.max_pages}"

                step_index, clicks_used, discovered = await explore_page(
                    sess=sess,
                    rec=rec,
                    builder=builder,
                    url=current,
                    dwell=cfg.dwell,
                    initial_wait=cfg.initial_wait,
                    click_budget=clicks_remaining,
                    click_timeout_ms=cfg.click_timeout_ms,
                    deadline_monotonic=deadline,
                    step_index=step_index,
                    step_annotations=step_annotations,
                    page_label=page_label,
                    target_highlight=cfg.target_highlight,
                    pre_click_highlight_frames=(
                        cfg.pre_click_highlight_frames if cfg.target_highlight else 0
                    ),
                    grid=cfg.annotate.grid,
                )
                clicks_remaining -= clicks_used

                # First page must have discovered elements; downstream pages
                # can be scroll-only (a legitimate destination).
                if pages_visited == 1 and step_index == 1:
                    _die("no interactive elements discovered on start page")

                # Seeded tours don't auto-enqueue: the caller specified the
                # exact path and shouldn't be surprised by extra URLs.
                new_enqueued = 0
                if not seeded:
                    for candidate in discovered:
                        if not is_same_origin(candidate, cfg.url):
                            continue
                        if normalize_url(candidate) in visited:
                            continue
                        queue.append(candidate)
                        new_enqueued += 1
                log.info(
                    "%s · budget: %d clicks left, queue: %d urls (+%d new)",
                    page_label,
                    clicks_remaining,
                    len(queue),
                    new_enqueued,
                )

            log.info("BFS done. Flushing %d step annotations...", len(step_annotations))
            rec.flush()
            if cfg.zoom_on_click_factor is not None:
                zoomed = apply_zoom_on_click(
                    rec.frames_dir,
                    factor=cfg.zoom_on_click_factor,
                    frames_after_click=cfg.zoom_frames_after_click,
                )
                log.info(
                    "zoomed %d frames (factor=%.1fx, %d frames after each click)",
                    zoomed,
                    cfg.zoom_on_click_factor,
                    cfg.zoom_frames_after_click,
                )
            inserted = interpolate_cursor_motion(rec.frames_dir, cfg.annotate.cursor_style)
            if inserted:
                log.info(
                    "interpolated %d frames (n=%d per gap, easing=%s, min_distance=%d)",
                    inserted,
                    cfg.annotate.cursor_style.interpolate_frames,
                    cfg.annotate.cursor_style.interpolate_easing,
                    cfg.annotate.cursor_style.interpolate_min_distance,
                )
            log.info("annotating frames...")
            annotate_frames_dir(rec.frames_dir, steps=step_annotations, config=cfg.annotate)
            # Bookend the annotated frames with title / summary cards so a
            # human viewer sees a titled entry beat and a stats-summary
            # tail. Cards render at the browser's viewport size so they
            # match the surrounding frames exactly. The prepend also masks
            # any pre-first-paint white flash (#68/#129 Track G). Deferred
            # bookend-only tour totals go in the summary card. See #129 E.
            if cfg.title_card or cfg.summary_card:
                card_size = _card_size_for(cfg, rec.frames_dir)
                if cfg.title_card:
                    _prepend_title_card(rec.frames_dir, cfg, card_size)
                if cfg.summary_card:
                    _append_summary_card(
                        rec.frames_dir,
                        cfg,
                        card_size,
                        pages_visited=pages_visited,
                        clicks=cfg.max_steps - clicks_remaining,
                        tour_elapsed_s=time.monotonic() - tour_started,
                    )
            log.info("encoding %s...", cfg.out)
            out_path = Path(cfg.out)
            enc = encode(
                rec.frames_dir,
                out_path,
                fps=cfg.fps,
                quality=cfg.quality,
                loop=cfg.loop,
                format=cfg.format,  # type: ignore[arg-type]
            )
            media = _make_media(enc, cfg.fps)
            sidecar = _write_sidecar(
                out_path,
                cfg.no_sidecar,
                builder,
                media,
                with_feedback=cfg.with_feedback,
                redact_patterns=cfg.redact_patterns,
                strip_query_strings=cfg.strip_query_strings,
            )

    tour_elapsed = time.monotonic() - tour_started
    total_clicks = cfg.max_steps - clicks_remaining
    typer.echo(
        f"✔ {enc.path} ({enc.size_bytes // 1024} KB, {enc.frame_count} frames, "
        f"{enc.duration_s:.1f}s reel, {pages_visited} page(s), "
        f"{total_clicks} clicks, wall {tour_elapsed:.1f}s)"
    )
    if sidecar:
        typer.echo(f"  sidecar: {sidecar}")
    # See #151 (AI-4): optional machine-readable summary for JSONL parsers.
    # Off by default — no-op emit, no extra blank line. On by --emit-events,
    # exactly one JSON object per tour, keyed by ``event`` so future event
    # types (per-step or advisory streams) can share the same channel.
    if cfg.emit_events:
        _emit_tour_complete(
            gif_path=str(enc.path),
            frames=enc.frame_count,
            duration_s=enc.duration_s,
            pages=pages_visited,
            clicks=total_clicks,
            wall_s=tour_elapsed,
            sidecar_path=str(sidecar) if sidecar else None,
        )
    # Track A of #138: heuristic advisories on the completed tour. Prints to
    # stderr with a ⚠ marker so an AI parsing the run's output can act on
    # them; each carries a stable id + doc URL for programmatic follow-up.
    if builder is not None:
        nav_clicks = _count_nav_clicks(builder.steps)
        for adv in build_advisories(
            builder.steps,
            media,
            total_clicks=total_clicks,
            nav_clicks=nav_clicks,
            annotate_cfg=cfg.annotate,
        ):
            print(f"⚠ {adv.message} — see {adv.doc_url}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Small helpers — kept private so the module's public API stays tight.
# ---------------------------------------------------------------------------


def _die(msg: str, code: int = 1) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _emit_tour_complete(
    *,
    gif_path: str,
    frames: int,
    duration_s: float,
    pages: int,
    clicks: int,
    wall_s: float,
    sidecar_path: str | None,
) -> None:
    """Print one JSON object on its own line to stdout (see #151 AI-4).

    Keyed by ``event`` so future event types (per-step, advisories) can
    share the same channel without breaking downstream JSONL parsers.
    Rounding matches the prose summary's precision (1 decimal on seconds)
    so a human diff'ing the two lines sees consistent numbers.
    """
    import json as _json

    payload: dict[str, Any] = {
        "event": "tour_complete",
        "gif_path": gif_path,
        "frames": frames,
        "duration_s": round(duration_s, 1),
        "pages": pages,
        "clicks": clicks,
        "wall_s": round(wall_s, 1),
        "sidecar_path": sidecar_path,
    }
    typer.echo(_json.dumps(payload))


def _count_nav_clicks(steps: list[StepReport]) -> int:
    """Count click steps whose post-action URL differs from the prior step's.

    Used to feed :func:`clickcast.feedback.build_advisories` the tour-level
    nav-ratio signal without threading a new counter through ``explore_page``.
    Only ``ok`` click steps count — failed clicks can't have caused a nav.
    """
    nav = 0
    prev_url = None
    for step in steps:
        state = step.page_state
        current_url = state.url_after if state is not None else None
        if (
            step.action == "click"
            and step.status == "ok"
            and prev_url
            and current_url
            and current_url != prev_url
        ):
            nav += 1
        if current_url:
            prev_url = current_url
    return nav


def _make_media(enc: Any, fps: int) -> Media:
    return Media(
        path=str(enc.path),
        format=enc.format,
        size_bytes=enc.size_bytes,
        frame_count=enc.frame_count,
        duration_s=enc.duration_s,
        fps=fps,
    )


def _write_sidecar(
    out: Path,
    no_sidecar: bool,
    builder: ReportBuilder | None,
    media: Media,
    *,
    with_feedback: bool = False,
    redact_patterns: list[re.Pattern[str]] | None = None,
    strip_query_strings: bool = False,
) -> Path | None:
    if no_sidecar or builder is None:
        return None
    sidecar = out.with_suffix(out.suffix + ".json")
    report = builder.build(media)
    write_report(
        report,
        sidecar,
        with_feedback=with_feedback,
        redact_patterns=redact_patterns,
        strip_query_strings=strip_query_strings,
    )
    return sidecar


# ---------------------------------------------------------------------------
# Cards helpers (Track E of #129).
#
# Cards are inserted AFTER the annotator pass so they don't gain progress
# bars, cursor trails, or action-panel overlays — they're standalone frames
# that bookend the reel. Insertion is a two-step edit to frames.json:
# prepend / append the card frame entries in the manifest, and the encoder
# picks them up on its next pass.
# ---------------------------------------------------------------------------


def _card_size_for(cfg: AutoConfig, frames_dir: Path) -> tuple[int, int]:
    """Pick a card size that matches the surrounding frames.

    Prefer the recorded frames' actual pixel size (accounts for zoom, DPR
    tweaks, and post-capture resizes). Fall back to the browser viewport
    if the frames-dir is unexpectedly empty.
    """
    from PIL import Image

    for f in sorted(frames_dir.glob("frame-*.png")):
        try:
            with Image.open(f) as img:
                return img.size
        except OSError:
            continue
    vp = cfg.session_kwargs.get("viewport")
    if isinstance(vp, tuple) and len(vp) == 2:
        return (int(vp[0]), int(vp[1]))
    return (1280, 800)


def _prepend_title_card(frames_dir: Path, cfg: AutoConfig, size: tuple[int, int]) -> None:
    title = cfg.title_card_text or _default_title_for(cfg.url)
    paths = render_title_card(
        frames_dir,
        title=title,
        subtitle=cfg.url,
        size=size,
        frame_count=cfg.title_card_frames,
        style=cfg.card_style,
    )
    _splice_manifest(frames_dir, [p.name for p in paths], where="prepend")
    log.info("prepended title card: %d frames", len(paths))


def _append_summary_card(
    frames_dir: Path,
    cfg: AutoConfig,
    size: tuple[int, int],
    *,
    pages_visited: int,
    clicks: int,
    tour_elapsed_s: float,
) -> None:
    stats = SummaryStats(
        pages=pages_visited,
        clicks=clicks,
        duration_s=tour_elapsed_s,
        watermark=cfg.summary_card_watermark,
    )
    paths = render_summary_card(
        frames_dir,
        stats=stats,
        size=size,
        frame_count=cfg.summary_card_frames,
        style=cfg.card_style,
    )
    _splice_manifest(frames_dir, [p.name for p in paths], where="append")
    log.info("appended summary card: %d frames", len(paths))


def _default_title_for(url: str) -> str:
    """Extract a friendly hostname-based title from a URL."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or url
    return f"clickcast tour · {host}"


def _splice_manifest(frames_dir: Path, filenames: list[str], *, where: str) -> None:
    """Prepend or append a list of frame filenames to ``frames.json``.

    Card frames carry synthetic step_index values that sit outside the
    range the annotator saw — either negative-adjacent (prepended) or one
    past the last real step (appended). ``cursor_xy`` is ``None`` so the
    cursor overlay never re-runs against them (the annotator won't be
    called on cards anyway, but keeping the field clean means the
    manifest is safe to re-annotate).
    """
    import json

    manifest_path = frames_dir / "frames.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    existing = manifest.get("frames", [])
    real_max = max((f["step_index"] for f in existing), default=-1)
    if where == "prepend":
        # Card frames get a single dedicated step_index that sits BEFORE
        # every real step. Shift real step indices up by one so the
        # progress bar's fraction (step_index+1 / total_steps) starts at
        # exactly the first real step, not partway through.
        shift = 1
        new_entries = [
            {
                "path": name,
                "step_index": 0,
                "sub_index": i,
                "cursor_xy": None,
            }
            for i, name in enumerate(filenames)
        ]
        shifted = [{**f, "step_index": f["step_index"] + shift} for f in existing]
        manifest["frames"] = new_entries + shifted
    elif where == "append":
        # Card sits at real_max + 1 — one past the last real step.
        step_idx = real_max + 1
        new_entries = [
            {
                "path": name,
                "step_index": step_idx,
                "sub_index": i,
                "cursor_xy": None,
            }
            for i, name in enumerate(filenames)
        ]
        manifest["frames"] = existing + new_entries
    else:
        raise ValueError(f"where must be 'prepend' or 'append', got {where!r}")
    manifest["count"] = len(manifest["frames"])
    manifest_path.write_text(json.dumps(manifest, indent=2))
