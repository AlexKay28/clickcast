"""Coverage for the AI-eye overlays added in #57.

Two things landed:
- `AnnotateConfig.label.style` — 'dark' (old) vs 'light' (default; readable on
  dark-mode sites like react.dev).
- `AnnotateConfig.actions_panel` — top-right side panel listing recent step
  labels with the current one highlighted.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

from clickcast.annotate import AnnotateConfig, Annotator, LabelStyle


def _make_frame(path: Path, size: tuple[int, int] = (600, 400)) -> Path:
    """Solid dark background — worst-case for the old dark label style."""
    Image.new("RGB", size, color=(20, 20, 25)).save(path, format="PNG")
    return path


def _dominant_color(path: Path, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """Return the average RGB inside `box` — cheap way to tell dark from light."""
    with Image.open(path) as img:
        crop = img.crop(box).convert("RGB")
    px = list(crop.getdata())
    r = sum(p[0] for p in px) // len(px)
    g = sum(p[1] for p in px) // len(px)
    b = sum(p[2] for p in px) // len(px)
    return (r, g, b)


class TestLabelStyle:
    def test_light_style_default_is_bright(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        out = Annotator().annotate(
            src,
            out_path=tmp_path / "annotated.png",
            step_index=0,
            total_steps=1,
            label="hello",
        )
        w, h = 600, 400
        box = (w // 2 - 40, h - 80, w // 2 + 40, h - 55)
        avg = _dominant_color(out, box)
        # Alpha=230 over the (20,20,25) dark page composites to ~147 —
        # dramatically lighter than the surrounding dark page and clearly
        # readable, which is the whole point of the light style.
        assert min(avg) > 130, f"expected light-mode label bg (>130 avg), got {avg}"

    def test_dark_style_produces_dark_label(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        cfg = AnnotateConfig(label=LabelStyle(style="dark"))
        out = Annotator(cfg).annotate(
            src,
            out_path=tmp_path / "annotated.png",
            step_index=0,
            total_steps=1,
            label="hello",
        )
        w, h = 600, 400
        box = (w // 2 - 40, h - 80, w // 2 + 40, h - 55)
        avg = _dominant_color(out, box)
        assert max(avg) < 80, f"expected dark-mode label bg (<80 avg), got {avg}"

    def test_explicit_colors_override_style(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        cfg = AnnotateConfig(
            label=LabelStyle(
                style="light",
                bg_color=(200, 0, 0, 255),
                fg_color=(255, 255, 255, 255),
            ),
        )
        out = Annotator(cfg).annotate(
            src,
            out_path=tmp_path / "annotated.png",
            step_index=0,
            total_steps=1,
            label="hello",
        )
        w, h = 600, 400
        box = (w // 2 - 40, h - 80, w // 2 + 40, h - 55)
        avg = _dominant_color(out, box)
        assert avg[0] > 100 and avg[1] < 60 and avg[2] < 60, f"expected reddish label bg, got {avg}"


class TestActionsPanel:
    def test_panel_renders_in_top_right(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        labels = ["open x.com", "click About", "click Docs", "scroll"]

        no_panel_out = Annotator(AnnotateConfig(actions_panel=False)).annotate(
            src,
            out_path=tmp_path / "no_panel.png",
            step_index=2,
            total_steps=4,
            label="click Docs",
            all_labels=labels,
        )
        with_panel_out = Annotator().annotate(
            src,
            out_path=tmp_path / "with_panel.png",
            step_index=2,
            total_steps=4,
            label="click Docs",
            all_labels=labels,
        )
        with Image.open(no_panel_out) as a, Image.open(with_panel_out) as b:
            top_right = (400, 0, 600, 150)
            diff = ImageChops.difference(
                a.crop(top_right).convert("RGB"),
                b.crop(top_right).convert("RGB"),
            ).getbbox()
        assert diff is not None, "actions panel did not draw distinguishable pixels"

    def test_panel_hidden_when_all_labels_empty(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")

        no_labels = Annotator().annotate(
            src,
            out_path=tmp_path / "no_labels.png",
            step_index=0,
            total_steps=1,
            label="hi",
            all_labels=None,
        )
        panel_off = Annotator(AnnotateConfig(actions_panel=False)).annotate(
            src,
            out_path=tmp_path / "panel_off.png",
            step_index=0,
            total_steps=1,
            label="hi",
            all_labels=["hi"],
        )
        with Image.open(no_labels) as a, Image.open(panel_off) as b:
            top_right = (400, 0, 600, 150)
            diff = ImageChops.difference(
                a.crop(top_right).convert("RGB"),
                b.crop(top_right).convert("RGB"),
            ).getbbox()
        assert diff is None, f"top-right should be identical when panel is off, diff={diff}"

    def test_current_row_highlighted(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        labels = ["a", "b", "c", "d", "e"]

        step0 = Annotator().annotate(
            src,
            out_path=tmp_path / "step0.png",
            step_index=0,
            total_steps=5,
            label="a",
            all_labels=labels,
        )
        step4 = Annotator().annotate(
            src,
            out_path=tmp_path / "step4.png",
            step_index=4,
            total_steps=5,
            label="e",
            all_labels=labels,
        )
        with Image.open(step0) as a, Image.open(step4) as b:
            top_right = (400, 0, 600, 200)
            diff = ImageChops.difference(
                a.crop(top_right).convert("RGB"),
                b.crop(top_right).convert("RGB"),
            ).getbbox()
        assert diff is not None, "changing step_index should shift the highlighted row"
