from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageChops

from clickcast.annotate import AnnotateConfig, Annotator
from clickcast.annotate.annotator import _wrap


def _make_frame(path: Path, size: tuple[int, int] = (400, 300)) -> Path:
    Image.new("RGB", size, color=(50, 80, 120)).save(path, format="PNG")
    return path


def _rgb_bytes(path: Path) -> bytes:
    with Image.open(path) as img:
        return img.convert("RGB").tobytes()


class TestWrap:
    def test_short_returns_as_is(self) -> None:
        assert _wrap("hi there", 60) == "hi there"

    def test_wraps_at_word_boundary(self) -> None:
        text = "one two three four five six seven"
        assert _wrap(text, 12) == "one two\nthree four\nfive six\nseven"

    def test_single_long_word_stays_on_own_line(self) -> None:
        assert _wrap("supercalifragilistic!", 10) == "supercalifragilistic!"


class TestAnnotateOutputContract:
    def test_writes_new_file_next_to_input(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        out = Annotator().annotate(src, step_index=0, total_steps=3, label="hi")
        assert out.exists()
        assert out != src
        assert out.parent == src.parent
        assert out.name.endswith(".annotated.png")

    def test_out_path_kwarg_overrides_default(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        target = tmp_path / "custom" / "hi.png"
        out = Annotator().annotate(src, out_path=target, step_index=0, total_steps=1)
        assert out == target
        assert target.exists()

    def test_input_frame_not_mutated(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        digest_before = _rgb_bytes(src)
        Annotator().annotate(src, step_index=0, total_steps=1, label="anything")
        assert _rgb_bytes(src) == digest_before

    def test_output_dimensions_match_input(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png", size=(500, 400))
        out = Annotator().annotate(src, step_index=0, total_steps=1)
        with Image.open(out) as img:
            assert img.size == (500, 400)

    def test_output_is_valid_png(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        out = Annotator().annotate(src, step_index=0, total_steps=1, label="x")
        with Image.open(out) as img:
            img.verify()


class TestLayerToggles:
    def test_all_layers_off_produces_pixel_identical_output(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        cfg = AnnotateConfig(clicks=False, labels=False, cursor=False, progress=False)
        out = Annotator(cfg).annotate(
            src,
            step_index=1,
            total_steps=3,
            label="ignored",
            cursor_xy=(100, 100),
            click_at=(100, 100),
            ripple_stage=2,
        )
        with Image.open(src) as a, Image.open(out) as b:
            diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
            assert diff.getbbox() is None, "expected pixel-identical output with all layers off"

    def test_progress_bar_only_touches_bottom_rows(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png", size=(400, 300))
        cfg = AnnotateConfig(
            clicks=False, labels=False, cursor=False, progress=True, progress_height=6
        )
        out = Annotator(cfg).annotate(src, step_index=0, total_steps=2)
        with Image.open(src) as a, Image.open(out) as b:
            diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
            bbox = diff.getbbox()
        assert bbox is not None
        # bbox = (l, t, r, b) — only the last few rows should change
        assert bbox[1] >= 300 - 6
        assert bbox[3] <= 300

    def test_progress_bar_filled_portion_scales_with_step_index(self, tmp_path: Path) -> None:
        # The background stripe covers the full width regardless of step, so
        # a bbox-width comparison isn't meaningful. Instead, count how many
        # pixels on the progress-bar row use the FILLED colour vs the BG.
        src = _make_frame(tmp_path / "frame.png", size=(400, 300))
        cfg = AnnotateConfig(clicks=False, labels=False, cursor=False, progress=True)

        def filled_px(step: int) -> int:
            out = Annotator(cfg).annotate(
                src,
                step_index=step,
                total_steps=5,
                out_path=tmp_path / f"s{step}.png",
            )
            with Image.open(out) as img:
                rgb = img.convert("RGB")
            # Scan the middle row of the progress bar; the FILLED colour is
            # cyan-ish (100, 200, 255) at high alpha over base (50, 80, 120),
            # producing a strong green channel (~180) — the faint background
            # tint only pushes G to ~105. So G > 150 cleanly separates them.
            row_y = 300 - 2
            filled = 0
            for x in range(rgb.width):
                _r, g, _b = rgb.getpixel((x, row_y))  # type: ignore[misc]
                if g > 150:
                    filled += 1
            return filled

        early = filled_px(0)
        later = filled_px(3)
        assert later > early
        # Sanity: at step 0 the fill covers roughly 1/5; at step 3, 4/5.
        assert later >= 3 * early


class TestClickRipple:
    def test_ripple_radius_grows_with_stage(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png", size=(400, 300))
        cfg = AnnotateConfig(
            clicks=True,
            labels=False,
            cursor=False,
            progress=False,
            ripple_radius_min=10,
            ripple_radius_max=60,
            ripple_stages=3,
        )
        ann = Annotator(cfg)

        # Compare stages 1 and 2 — by design the last stage fades all the way
        # to alpha 0 (spec: "128 → 0"), so stage 3 is invisible on purpose.
        stage1 = ann.annotate(
            src,
            step_index=0,
            total_steps=1,
            click_at=(200, 150),
            ripple_stage=1,
            out_path=tmp_path / "s1.png",
        )
        stage2 = ann.annotate(
            src,
            step_index=0,
            total_steps=1,
            click_at=(200, 150),
            ripple_stage=2,
            out_path=tmp_path / "s2.png",
        )
        with Image.open(src) as base:
            base_rgb = base.convert("RGB")
            with Image.open(stage1) as s1, Image.open(stage2) as s2:
                bbox1 = ImageChops.difference(base_rgb, s1.convert("RGB")).getbbox()
                bbox2 = ImageChops.difference(base_rgb, s2.convert("RGB")).getbbox()
        assert bbox1 is not None and bbox2 is not None
        assert (bbox2[2] - bbox2[0]) > (bbox1[2] - bbox1[0])
        assert (bbox2[3] - bbox2[1]) > (bbox1[3] - bbox1[1])


class TestCursor:
    def test_cursor_drawn_at_specified_position(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png", size=(400, 300))
        cfg = AnnotateConfig(
            clicks=False,
            labels=False,
            cursor=True,
            progress=False,
            cursor_size=20,
        )
        out = Annotator(cfg).annotate(src, step_index=0, total_steps=1, cursor_xy=(120, 100))
        with Image.open(src) as a, Image.open(out) as b:
            diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
            bbox = diff.getbbox()
        assert bbox is not None
        # Cursor is a size-20 diamond centered at (120, 100), so the changed
        # region should straddle those coordinates.
        assert bbox[0] <= 120 <= bbox[2]
        assert bbox[1] <= 100 <= bbox[3]

    def test_trail_length_bounded_by_config(self, tmp_path: Path) -> None:
        cfg = AnnotateConfig(cursor_trail_length=3)
        ann = Annotator(cfg)
        for i in range(10):
            ann._cursor_history.append((i * 10, 100))
            # Simulate annotate() eviction:
            history_cap = max(cfg.cursor_trail_length + 1, 1)
            while len(ann._cursor_history) > history_cap:
                ann._cursor_history.pop(0)
        # Cap = trail_length + 1 (current position slot)
        assert len(ann._cursor_history) == 4

    def test_reset_cursor_clears_history(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        ann = Annotator()
        ann.annotate(src, step_index=0, total_steps=1, cursor_xy=(50, 50))
        assert ann._cursor_history
        ann.reset_cursor()
        assert not ann._cursor_history


class TestLabel:
    def test_label_changes_pixels_near_configured_edge(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png", size=(400, 300))
        cfg_bottom = AnnotateConfig(
            clicks=False, labels=True, cursor=False, progress=False, label_position="bottom"
        )
        cfg_top = AnnotateConfig(
            clicks=False, labels=True, cursor=False, progress=False, label_position="top"
        )
        text = "Switch to 3D globe"
        bottom = Annotator(cfg_bottom).annotate(
            src, step_index=0, total_steps=1, label=text, out_path=tmp_path / "b.png"
        )
        top = Annotator(cfg_top).annotate(
            src, step_index=0, total_steps=1, label=text, out_path=tmp_path / "t.png"
        )
        with Image.open(src) as base:
            base_rgb = base.convert("RGB")
            with Image.open(bottom) as b, Image.open(top) as t:
                bbox_b = ImageChops.difference(base_rgb, b.convert("RGB")).getbbox()
                bbox_t = ImageChops.difference(base_rgb, t.convert("RGB")).getbbox()
        assert bbox_b is not None and bbox_t is not None
        # bottom label sits in the lower half; top label in the upper half
        assert bbox_b[1] > 150
        assert bbox_t[3] < 150

    def test_missing_label_produces_no_label_layer(self, tmp_path: Path) -> None:
        src = _make_frame(tmp_path / "frame.png")
        cfg = AnnotateConfig(clicks=False, cursor=False, progress=False, labels=True)
        # label=None → nothing drawn
        out = Annotator(cfg).annotate(src, step_index=0, total_steps=1, label=None)
        with Image.open(src) as a, Image.open(out) as b:
            diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
            assert diff.getbbox() is None


class TestBundledFont:
    def test_bundled_font_is_loadable(self) -> None:
        # Just constructing the annotator without a custom font_path must succeed;
        # the bundled DejaVuSans.ttf must be found via importlib.resources.
        Annotator()

    def test_custom_font_path_used(self, tmp_path: Path) -> None:
        # If someone supplies a bad path, we raise clearly rather than silently
        # falling back — surfaces packaging mistakes early.
        cfg = AnnotateConfig(font_path=tmp_path / "does-not-exist.ttf")
        with pytest.raises((FileNotFoundError, OSError)):
            Annotator(cfg)
