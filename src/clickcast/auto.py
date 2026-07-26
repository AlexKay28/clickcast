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
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer

from clickcast.annotate import (
    AnnotateConfig,
    StepAnnotation,
    annotate_frames_dir,
    apply_zoom_on_click,
    interpolate_cursor_motion,
)
from clickcast.capture import Recorder
from clickcast.core.actions import ClickStep, GotoStep, ScrollStep, execute
from clickcast.core.session import Session
from clickcast.discovery import discover
from clickcast.discovery.urlutil import is_same_origin, normalize_url
from clickcast.encode import encode
from clickcast.feedback import Media, ReportBuilder
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


# ---------------------------------------------------------------------------
# Per-page loop
# ---------------------------------------------------------------------------


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
) -> tuple[int, int, list[str]]:
    """Goto ``url``, discover, click up to ``click_budget`` elements, scroll.

    Returns ``(next_step_index, clicks_used, discovered_urls)``.
    """
    discovered_urls: list[str] = []
    page_started = time.monotonic()
    log.info("%s → open %s", page_label, url)

    goto = GotoStep(url=url, wait="networkidle", dwell=dwell)
    await rec.pre_action(sess)
    result = await execute(goto, sess)
    if not result.ok:
        typer.secho(f"  skipped {url}: {result.error}", fg=typer.colors.YELLOW, err=True)
        log.warning("%s · skipped: %s", page_label, result.error)
        return step_index, 0, discovered_urls
    if initial_wait > 0:
        log.debug("%s · held %.1fs after networkidle for hydration", page_label, initial_wait)
        await sess.wait(initial_wait)
    frames_goto = await rec.post_action(sess, result, goto)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · open")
    if builder:
        await builder.record_step(index=step_index, step=goto, result=result, frames=frames_goto)
    step_index += 1

    elements = await discover(sess, limit=_discovery_limit(click_budget))
    log.info(
        "%s · discovered %d elements, click budget: %d", page_label, len(elements), click_budget
    )
    if builder and step_index == 1:
        builder.set_discovered(elements[:click_budget])

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
        r = await execute(step, sess)
        frames_step = await rec.post_action(sess, r, step)
        step_annotations[step_index] = StepAnnotation(
            label=f"{page_label} · click · {step.label}" if step.label else f"{page_label} · click",
            click_at=r.cursor_xy if r.status == "ok" else None,
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

    scroll = ScrollStep(by=_SCROLL_DISTANCE_PX, dwell=dwell)
    log.info("%s · scroll +%dpx", page_label, _SCROLL_DISTANCE_PX)
    await rec.pre_action(sess)
    r = await execute(scroll, sess)
    frames_scroll = await rec.post_action(sess, r, scroll)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · scroll")
    if builder:
        await builder.record_step(index=step_index, step=scroll, result=r, frames=frames_scroll)
    step_index += 1

    log.info(
        "%s · done in %.1fs (%d clicks used, %d nav candidates)",
        page_label,
        time.monotonic() - page_started,
        clicked,
        len(discovered_urls),
    )
    return step_index, clicked, discovered_urls


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
    typer.echo(
        f"✔ {enc.path} ({enc.size_bytes // 1024} KB, {enc.frame_count} frames, "
        f"{enc.duration_s:.1f}s reel, {pages_visited} page(s), "
        f"{cfg.max_steps - clicks_remaining} clicks, wall {tour_elapsed:.1f}s)"
    )
    if sidecar:
        typer.echo(f"  sidecar: {sidecar}")


# ---------------------------------------------------------------------------
# Small helpers — kept private so the module's public API stays tight.
# ---------------------------------------------------------------------------


def _die(msg: str, code: int = 1) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


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
