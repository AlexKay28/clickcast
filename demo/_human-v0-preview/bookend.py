"""v0 human-demo bookender: strip white pre-paint frame, prepend title card,
append summary card. All via Pillow — no ffmpeg dep."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


SIZE = (1280, 800)
BG = (18, 22, 32)              # dark charcoal, matches react.dev dark
FG = (240, 240, 240)
ACCENT = (100, 200, 255)       # matches progress bar color
DIM = (140, 145, 160)

# Reuse the bundled DejaVuSans from clickcast for text consistency.
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in (
        Path("/home/minnesota/ClaudeSpace/clickcast/src/clickcast/annotate/fonts/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def make_card(lines: list[tuple[str, int, tuple[int, int, int]]]) -> Image.Image:
    """`lines` is [(text, font_size, color), ...]. Stacked vertically, centered."""
    img = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(img)
    # Vertical layout: measure total height, center.
    fonts = [(t, _load_font(sz), c) for t, sz, c in lines]
    gaps = [22 if i < len(fonts) - 1 else 0 for i in range(len(fonts))]
    heights = []
    for text, font, _ in fonts:
        bbox = draw.textbbox((0, 0), text, font=font)
        heights.append(bbox[3] - bbox[1])
    total_h = sum(heights) + sum(gaps)
    y = (SIZE[1] - total_h) // 2
    for (text, font, color), h, g in zip(fonts, heights, gaps, strict=False):
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        x = (SIZE[0] - w) // 2
        draw.text((x, y - bbox[1]), text, font=font, fill=color)
        y += h + g
    return img


def main() -> None:
    tour = Path("/tmp/human-demo/auto.gif")
    sidecar = json.loads(Path("/tmp/human-demo/auto.gif.json").read_text())
    fps = sidecar["media"]["fps"]
    n_clicks = sum(1 for s in sidecar["steps"] if s["action"] == "click" and s["status"] == "ok")
    n_errors = sum(1 for s in sidecar["steps"] if s["status"] != "ok")
    dur = sidecar["media"]["duration_s"]

    title = make_card([
        ("clickcast", 96, ACCENT),
        ("a tour of react.dev", 42, FG),
        ("annotated · reproducible · AI-readable", 24, DIM),
    ])
    summary = make_card([
        (f"{n_clicks} clicks  ·  {dur:.1f}s  ·  {n_errors} errors", 42, FG),
        ("full log in tour.gif.json", 24, DIM),
        ("github.com/AlexKay28/clickcast", 24, DIM),
    ])

    # Convert cards to palette matching the tour's palette space so LZW
    # compression stays reasonable. Simplest: pass RGB frames straight to
    # PIL save; it'll requantize on save.
    with Image.open(tour) as im:
        n = im.n_frames
        durations: list[int] = []
        tour_frames: list[Image.Image] = []
        for i in range(n):
            im.seek(i)
            # Drop the pre-paint white opener (frame 0) — it's the #68 bug.
            if i == 0:
                continue
            f = im.convert("RGB")
            tour_frames.append(f)
            info = im.info
            durations.append(int(info.get("duration", 1000 // fps)))

    frame_ms = int(1000 / fps)

    # Title card: hold 2.5s
    title_holds = max(1, int(round(2.5 * fps)))
    # Summary card: hold 3.0s
    summary_holds = max(1, int(round(3.0 * fps)))

    all_frames = [title] * title_holds + tour_frames + [summary] * summary_holds
    all_durs = [frame_ms] * title_holds + durations + [frame_ms] * summary_holds

    out = Path("/tmp/human-demo/tour-v0.gif")
    all_frames[0].save(
        out,
        save_all=True,
        append_images=all_frames[1:],
        duration=all_durs,
        loop=0,
        optimize=True,
        disposal=2,
    )
    print(f"wrote {out} · {len(all_frames)} frames · {out.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
