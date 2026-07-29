"""Config-only ceiling — uses ONLY fields shipped in clickcast v0.2.0.
No patch. No custom code. Just AnnotateConfig knobs that exist today."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path("/home/minnesota/ClaudeSpace/clickcast/src")))

from clickcast.annotate import AnnotateConfig, CursorStyle  # noqa: E402
from clickcast.auto import AutoConfig, run_tour  # noqa: E402

# Only shipped fields — no single_arrow, no panel.position.
CFG = AnnotateConfig(
    cursor_style=CursorStyle(
        interpolate=False,           # shipped in v0.2.0
        arrow_color=(255, 30, 30, 240),  # shipped
        arrow_thickness=5,           # shipped
        arrow_head_size=18,          # shipped
    ),
    # NB: panel stays at hard-coded top-right (no `position` field in v0.2.0)
)


async def main() -> None:
    await run_tour(
        AutoConfig(
            url="https://react.dev/",
            out="/tmp/human-demo/config-only.gif",
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
