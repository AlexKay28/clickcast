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
from pathlib import Path

from clickcast.capture import Recorder
from clickcast.core.actions import ClickStep, GotoStep, ScrollStep, execute
from clickcast.core.session import Session
from clickcast.discover import discover
from clickcast.encode import encode

log = logging.getLogger("clickcast.demo")


async def _run(
    *,
    url: str,
    out: Path,
    viewport: tuple[int, int],
    fps: int,
    dwell: float,
    max_clicks: int,
) -> None:
    async with Session(viewport=viewport) as sess:
        with Recorder(fps=fps, default_dwell=dwell) as rec:
            goto = GotoStep(url=url, wait="networkidle", dwell=dwell)
            await rec.pre_action(sess)
            result = await execute(goto, sess)
            await rec.post_action(sess, result, goto)
            if not result.ok:
                raise RuntimeError(f"goto {url} failed: {result.error}")

            elements = await discover(sess, limit=max_clicks * 2)
            log.info(
                "discovered %d elements: %s",
                len(elements),
                [f"{e.role}:{e.text[:24]}" for e in elements[:max_clicks]],
            )
            if not elements:
                raise RuntimeError("auto-discovery returned zero elements")

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
                await rec.pre_action(sess)
                r = await execute(step, sess)
                await rec.post_action(sess, r, step)
                if r.status == "ok":
                    clicked += 1
                await sess.wait(0.3)

            scroll = ScrollStep(by=600, dwell=dwell)
            await rec.pre_action(sess)
            r = await execute(scroll, sess)
            await rec.post_action(sess, r, scroll)

            paths = rec.flush()
            log.info("captured %d frames", len(paths))

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
        )
    )


if __name__ == "__main__":
    main()
