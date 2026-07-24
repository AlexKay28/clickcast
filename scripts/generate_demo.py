"""Generate the README demo GIF by auto-discovering + clicking on a live site.

Usage
-----

    python scripts/generate_demo.py \\
        --url https://worldsight-weld.vercel.app/ \\
        --out docs/demo.gif

Run either locally (`playwright install --with-deps chromium` first) or through
`.github/workflows/demo.yml` — the CI job wraps the same call.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import deque
from pathlib import Path

from clickcast.annotate import StepAnnotation, annotate_frames_dir
from clickcast.capture import Recorder
from clickcast.core.actions import ClickStep, GotoStep, ScrollStep, execute
from clickcast.core.session import Session
from clickcast.discovery import discover
from clickcast.discovery.urlutil import is_same_origin, normalize_url
from clickcast.encode import encode

log = logging.getLogger("clickcast.demo")


async def _tour_one_page(
    *,
    sess: Session,
    rec: Recorder,
    url: str,
    dwell: float,
    initial_wait: float,
    click_budget: int,
    step_index: int,
    step_annotations: dict[int, StepAnnotation],
    page_label: str,
) -> tuple[int, int, list[str]]:
    """Goto, discover, click up to `click_budget`, scroll.

    Returns `(next_step_index, clicks_used, discovered_urls)`.
    """
    discovered_urls: list[str] = []
    page_started = time.monotonic()
    log.info("%s → open %s", page_label, url)

    goto = GotoStep(url=url, wait="networkidle", dwell=dwell)
    await rec.pre_action(sess)
    result = await execute(goto, sess)
    if not result.ok:
        log.warning("%s · skipped: %s", page_label, result.error)
        return step_index, 0, discovered_urls
    if initial_wait > 0:
        await sess.wait(initial_wait)
        log.debug("%s · held %.1fs after networkidle for hydration", page_label, initial_wait)
    await rec.post_action(sess, result, goto)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · open")
    step_index += 1

    elements = await discover(sess, limit=max(click_budget * 2, 20))
    log.info(
        "%s · discovered %d elements, click budget: %d", page_label, len(elements), click_budget
    )

    clicked = 0
    for element in elements:
        if clicked >= click_budget:
            break
        step = ClickStep(
            selector=element.selector,
            dwell=dwell,
            optional=True,
            label=element.text[:40] or element.role,
        )
        url_before = sess.page.url
        log.info(
            "%s · click %d/%d · %s:%s",
            page_label,
            clicked + 1,
            click_budget,
            element.role,
            (element.text[:40] or "").strip() or element.selector,
        )
        await rec.pre_action(sess)
        r = await execute(step, sess)
        await rec.post_action(sess, r, step)
        step_annotations[step_index] = StepAnnotation(
            label=f"{page_label} · click · {step.label}" if step.label else f"{page_label} · click",
            click_at=r.cursor_xy if r.status == "ok" else None,
        )
        if r.status == "ok":
            clicked += 1
        else:
            log.warning("%s · click failed: %s", page_label, r.error)
        step_index += 1

        # Post-click nav: `domcontentloaded` + hard 5s timeout — `networkidle`
        # hung the react.dev demo for 30+ minutes because HMR/WebSockets never
        # let the network go idle.
        url_after = sess.page.url
        if url_after != url_before:
            discovered_urls.append(url_after)
            if not is_same_origin(url_after, url_before):
                log.info("%s · nav to cross-origin %s → bailing", page_label, url_after)
                break
            log.info("%s · nav to %s → going back", page_label, url_after)
            back_started = time.monotonic()
            try:
                await sess.page.go_back(wait_until="domcontentloaded", timeout=5000)
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
        await sess.wait(0.3)

    scroll = ScrollStep(by=600, dwell=dwell)
    log.info("%s · scroll +600px", page_label)
    await rec.pre_action(sess)
    r = await execute(scroll, sess)
    await rec.post_action(sess, r, scroll)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · scroll")
    step_index += 1

    log.info(
        "%s · done in %.1fs (%d clicks used, %d nav candidates)",
        page_label,
        time.monotonic() - page_started,
        clicked,
        len(discovered_urls),
    )
    return step_index, clicked, discovered_urls


async def _run(
    *,
    url: str,
    out: Path,
    viewport: tuple[int, int],
    fps: int,
    dwell: float,
    max_clicks: int,
    max_pages: int,
    initial_wait: float,
    keep_frames_dir: Path | None,
) -> None:
    async with Session(viewport=viewport) as sess:
        with Recorder(
            fps=fps,
            default_dwell=dwell,
            keep=keep_frames_dir is not None,
            out_dir=keep_frames_dir,
        ) as rec:
            step_annotations: dict[int, StepAnnotation] = {}
            step_index = 0
            visited: set[str] = set()
            queue: deque[str] = deque([url])
            pages_visited = 0
            clicks_remaining = max_clicks

            while queue and pages_visited < max_pages and clicks_remaining > 0:
                current = queue.popleft()
                key = normalize_url(current)
                if key in visited:
                    continue
                visited.add(key)
                pages_visited += 1
                page_label = f"page {pages_visited}/{max_pages}"

                step_index, clicks_used, discovered = await _tour_one_page(
                    sess=sess,
                    rec=rec,
                    url=current,
                    dwell=dwell,
                    initial_wait=initial_wait,
                    click_budget=clicks_remaining,
                    step_index=step_index,
                    step_annotations=step_annotations,
                    page_label=page_label,
                )
                clicks_remaining -= clicks_used
                if pages_visited == 1 and step_index == 1:
                    raise RuntimeError("auto-discovery returned zero elements on start page")

                for candidate in discovered:
                    if not is_same_origin(candidate, url):
                        continue
                    if normalize_url(candidate) in visited:
                        continue
                    queue.append(candidate)

            paths = rec.flush()
            log.info("captured %d frames across %d page(s)", len(paths), pages_visited)

            annotated = annotate_frames_dir(rec.frames_dir, steps=step_annotations)
            log.info("annotated %d frames (click ripples + step counter + labels)", annotated)

            out.parent.mkdir(parents=True, exist_ok=True)
            result_media = encode(rec.frames_dir, out, fps=fps, format="gif", quality=8)
            log.info(
                "encoded %s (%d KB, %d frames, %.1fs)",
                result_media.path,
                result_media.size_bytes // 1024,
                result_media.frame_count,
                result_media.duration_s,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://worldsight-weld.vercel.app/")
    parser.add_argument("--out", type=Path, default=Path("docs/demo.gif"))
    parser.add_argument("--viewport", default="1280x800")
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument(
        "--dwell",
        type=float,
        default=0.5,
        help="Seconds to hold each captured screen. Keep short (<= 0.5s) so the reel stays snappy.",
    )
    parser.add_argument(
        "--max-clicks",
        type=int,
        default=15,
        help="Total click budget across the whole tour (sum across every visited page).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="Cap on how many pages the tour visits (including the start URL).",
    )
    parser.add_argument(
        "--initial-wait",
        type=float,
        default=4.0,
        help="Seconds to hold after networkidle before starting to click (SPA hydration).",
    )
    parser.add_argument(
        "--keep-frames",
        type=Path,
        default=None,
        help="If set, copy raw PNG frames to this directory (for debugging).",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    w, h = args.viewport.lower().split("x")
    asyncio.run(
        _run(
            url=args.url,
            out=args.out,
            viewport=(int(w), int(h)),
            fps=args.fps,
            dwell=args.dwell,
            max_clicks=args.max_clicks,
            max_pages=args.max_pages,
            initial_wait=args.initial_wait,
            keep_frames_dir=args.keep_frames,
        )
    )


if __name__ == "__main__":
    main()
