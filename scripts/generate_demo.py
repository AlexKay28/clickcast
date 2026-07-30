"""Generate the README demo GIF by auto-discovering + clicking on a live site.

Thin wrapper around `clickcast.auto.run_tour` — same engine the `clickcast auto`
CLI uses, so the demo GIF stays in sync with the tool's actual behavior.

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

from clickcast.auto import AutoConfig, run_tour
from clickcast.core.viewport import Viewport

log = logging.getLogger("clickcast.demo")


_PACE_TABLE = {
    "fast": (15, 0.15),
    "natural": (12, 0.4),
    "slow": (10, 0.7),
    "onboarding": (8, 1.2),
}


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
        "--max-duration",
        type=float,
        default=120.0,
        help="Hard wall-time cap on the whole tour in seconds.",
    )
    parser.add_argument(
        "--click-timeout",
        type=float,
        default=5.0,
        help="Per-click timeout in seconds (Playwright default is 30s).",
    )
    parser.add_argument(
        "--traversal",
        choices=("dfs", "bfs"),
        default="dfs",
        help=(
            "Queue policy: 'dfs' (default, coherent narrative) or 'bfs' (site-map "
            "coverage, visit every top-level destination first)."
        ),
    )
    parser.add_argument(
        "--pace",
        choices=("fast", "natural", "slow", "onboarding"),
        default=None,
        help=(
            "Speed preset — overrides --fps and --dwell defaults when set. "
            "fast: fps=15 dwell=0.15 | natural: fps=12 dwell=0.4 | "
            "slow: fps=10 dwell=0.7 | onboarding: fps=8 dwell=1.2."
        ),
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

    if args.pace:
        preset_fps, preset_dwell = _PACE_TABLE[args.pace]
        if args.fps == 12:
            args.fps = preset_fps
        if args.dwell == 0.5:
            args.dwell = preset_dwell
        log.info("resolved --pace=%s → fps=%d dwell=%.2fs", args.pace, args.fps, args.dwell)

    vp = Viewport.parse(args.viewport)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(
        run_tour(
            AutoConfig(
                url=args.url,
                out=str(args.out),
                max_steps=args.max_clicks,
                max_pages=args.max_pages,
                max_duration=args.max_duration,
                click_timeout_ms=int(args.click_timeout * 1000),
                traversal=args.traversal,
                seed_urls=[],
                dwell=args.dwell,
                initial_wait=args.initial_wait,
                session_kwargs={"viewport": vp.as_tuple()},
                fps=args.fps,
                format="gif",
                quality=8,
                loop=0,
                no_sidecar=False,
            )
        )
    )


if __name__ == "__main__":
    main()
