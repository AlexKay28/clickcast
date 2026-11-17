#!/usr/bin/env python3
"""Render the GitHub social preview card (1280x640 PNG).

GitHub's social preview is uploaded by hand (Settings -> Social preview), but
the image should not be a mystery blob in the repository: this script is the
source of truth, so the card can be regenerated and reviewed like any other
asset.

    python scripts/make_social_preview.py

Writes ``docs/assets/social-card.png``. Needs Pillow (a clickcast runtime
dependency) and a DejaVu-family font; falls back to PIL's bundled font when
none is present.
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 640
BG = (17, 21, 31)
GLOW = (28, 38, 56)
INK = (238, 242, 248)
MUTED = (150, 164, 184)
ACCENT = (232, 66, 74)
BLUE = (86, 156, 214)
OUTLINE = (64, 78, 100)

OUT = pathlib.Path(__file__).parent.parent / "docs" / "assets" / "social-card.png"

_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts",
]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for directory in _FONT_DIRS:
        try:
            return ImageFont.truetype(f"{directory}/{name}", size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def main() -> pathlib.Path:
    card = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(card)

    # Soft vertical glow so the card does not read as a flat rectangle.
    for y in range(HEIGHT):
        weight = 1 - (y / HEIGHT)
        draw.line(
            [(0, y), (WIDTH, y)],
            fill=tuple(int(b + (g - b) * weight * 0.3) for b, g in zip(BG, GLOW, strict=False)),
        )

    # Left: record dot + wordmark.
    draw.ellipse((72, 96, 108, 132), fill=ACCENT)
    draw.text((132, 72), "clickcast", font=_font("DejaVuSans-Bold.ttf", 104), fill=INK)

    subtitle_font = _font("DejaVuSansMono.ttf", 30)
    draw.text((76, 212), "Playwright reels + a JSON", font=subtitle_font, fill=MUTED)
    draw.text((76, 254), "sidecar for AI agents", font=subtitle_font, fill=MUTED)

    # Tag pills.
    label_font = _font("DejaVuSans.ttf", 24)
    x = 76
    for text, width in (("MCP", 96), ("GIF/MP4", 132), ("JSON sidecar", 208), ("Python", 132)):
        draw.rounded_rectangle((x, 330, x + width, 386), radius=28, outline=OUTLINE, width=2)
        draw.text((x + 20, 346), text, font=label_font, fill=INK)
        x += width + 18

    # Right: browser chrome + filmstrip + timeline.
    draw.rounded_rectangle((820, 120, 1208, 470), radius=14, outline=OUTLINE, width=3)
    draw.line([(820, 168), (1208, 168)], fill=OUTLINE, width=3)
    for i, colour in enumerate(((232, 66, 74), (232, 180, 74), (86, 190, 120))):
        cx = 852 + i * 30
        draw.ellipse((cx - 7, 137, cx + 7, 151), fill=colour)

    for i in range(4):
        left = 848 + i * 88
        draw.rounded_rectangle(
            (left, 208, left + 72, 400), radius=8, fill=(31, 46, 66), outline=(56, 84, 120), width=2
        )
        draw.rectangle((left + 8, 224, left + 64, 384), fill=(38, 58, 84))
        draw.rectangle((left + 8, 224, left + 64, 248), fill=BLUE)

    draw.line([(848, 438), (1180, 438)], fill=(56, 84, 120), width=4)
    draw.ellipse((1010, 430, 1030, 450), fill=ACCENT)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    card.save(OUT, "PNG", optimize=True)
    return OUT


if __name__ == "__main__":
    print(main())
