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
    max_clicks: int,
    step_index: int,
    step_annotations: dict[int, StepAnnotation],
    page_label: str,
) -> tuple[int, list[str]]:
    """Goto, discover, click up to N, scroll. Returns the updated step_index
    and any URLs the browser navigated to during clicks."""
    discovered_urls: list[str] = []

    goto = GotoStep(url=url, wait="networkidle", dwell=dwell)
    await rec.pre_action(sess)
    result = await execute(goto, sess)
    if not result.ok:
        log.warning("skipped %s: %s", url, result.error)
        return step_index, discovered_urls
    if initial_wait > 0:
        await sess.wait(initial_wait)
        log.info("held %.1fs after networkidle for hydration", initial_wait)
    await rec.post_action(sess, result, goto)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · open")
    step_index += 1

    elements = await discover(sess, limit=max_clicks * 2)
    log.info(
        "%s discovered %d elements: %s",
        page_label,
        len(elements),
        [f"{e.role}:{e.text[:24]}" for e in elements[:max_clicks]],
    )

    clicked = 0
    for element in elements:
        if clicked >= max_clicks:
            break
        step = ClickStep(
            selector=element.selector,
            dwell=dwell,
            optional=True,
            label=element.text[:40] or element.role,
        )
        url_before = sess.page.url
        await rec.pre_action(sess)
        r = await execute(step, sess)
        await rec.post_action(sess, r, step)
        step_annotations[step_index] = StepAnnotation(
            label=f"{page_label} · click · {step.label}" if step.label else f"{page_label} · click",
            click_at=r.cursor_xy if r.status == "ok" else None,
        )
        if r.status == "ok":
            clicked += 1
        step_index += 1

        url_after = sess.page.url
        if url_after != url_before:
            discovered_urls.append(url_after)
            break
        await sess.wait(0.3)

    scroll = ScrollStep(by=600, dwell=dwell)
    await rec.pre_action(sess)
    r = await execute(scroll, sess)
    await rec.post_action(sess, r, scroll)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · scroll")
    step_index += 1

    return step_index, discovered_urls


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

            while queue and pages_visited < max_pages:
                current = queue.popleft()
                key = normalize_url(current)
                if key in visited:
                    continue
                visited.add(key)
                pages_visited += 1
                page_label = f"page {pages_visited}/{max_pages}"

                step_index, discovered = await _tour_one_page(
                    sess=sess,
                    rec=rec,
                    url=current,
                    dwell=dwell,
                    initial_wait=initial_wait,
                    max_clicks=max_clicks,
                    step_index=step_index,
                    step_annotations=step_annotations,
                    page_label=page_label,
                )
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
    parser.add_argument("--dwell", type=float, default=1.2)
    parser.add_argument("--max-clicks", type=int, default=3)
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
