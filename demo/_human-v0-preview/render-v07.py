"""v0.5 render — custom AnnotateConfig with bottom-right panel + single-arrow cursor."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure we import the modified annotator.
sys.path.insert(0, str(Path("/home/minnesota/ClaudeSpace/clickcast/src")))

from clickcast.annotate import (  # noqa: E402
    ActionsPanelStyle,
    AnnotateConfig,
    CursorStyle,
)
from clickcast.auto import AutoConfig, run_tour  # noqa: E402

CFG = AnnotateConfig(
    # bright-red single arrow, thicker + bigger head so a human eye tracks it
    cursor_style=CursorStyle(
        single_arrow=True,
        interpolate=False,
        arrow_color=(255, 30, 30, 240),
        arrow_thickness=5,
        arrow_head_size=18,
    ),
    # move panel out of react.dev's top-nav click zone
    panel=ActionsPanelStyle(position="bottom-right"),
)


async def main() -> None:
    await run_tour(
        AutoConfig(
            url="https://react.dev/",
            out="/tmp/human-demo/auto-v07.gif",
            max_steps=5,
            max_pages=2,
            max_duration=240.0,
            click_timeout_ms=15_000,
            dwell=1.2,
            initial_wait=5.0,
            session_kwargs={
                "engine": "chromium",
                "viewport": (1280, 800),
                "device": None,
                "headful": False,
                "lang": None,
                "dark": False,
                "slowmo": 0,
            },
            fps=8,
            quality=8,
            loop=0,
            no_sidecar=False,
            traversal="dfs",
            zoom_on_click_factor=2.5,
            annotate=CFG,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
