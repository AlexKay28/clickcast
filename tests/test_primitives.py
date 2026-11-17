"""Tests for declarative region-annotation drawing primitives."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageChops

from clickcast.annotate.primitives import (
    RegionAnnotation,
    annotations_for_step,
    draw_annotations,
    load_annotations,
)

BASE = (40, 80, 120, 255)


def _canvas(size: tuple[int, int] = (100, 80)) -> Image.Image:
    return Image.new("RGBA", size, BASE)


def _draw(spec: RegionAnnotation, size: tuple[int, int] = (100, 80)) -> Image.Image:
    canvas = _canvas(size)
    result = draw_annotations(canvas, [spec], step_index=0)
    assert result is canvas
    return result


def _difference(before: Image.Image, after: Image.Image) -> Image.Image:
    # RGBA.getbbox() considers only alpha by default; RGB differences are what
    # matter when compositing onto these opaque test frames.
    return ImageChops.difference(before, after).convert("RGB")


class TestRectPrimitive:
    def test_outline_and_translucent_fill(self) -> None:
        red = (255, 0, 0, 255)
        out = _draw(
            RegionAnnotation(
                kind="rect",
                bbox=(10, 10, 40, 30),
                color=red,
                fill=(0, 255, 0, 128),
                radius=0,
                width=2,
            )
        )
        assert out.getpixel((10, 20)) == red
        assert out.getpixel((25, 25)) != BASE
        assert out.getpixel((25, 25))[:3] != red[:3]

    def test_outline_only_by_default(self) -> None:
        out = _draw(RegionAnnotation(kind="rect", bbox=(10, 10, 40, 30)))
        assert out.getpixel((25, 25)) == BASE
        assert out.getpixel((10, 20)) == RegionAnnotation(kind="rect", bbox=(0, 0, 1, 1)).color


class TestCirclePrimitive:
    def test_ellipse_outline_and_fill(self) -> None:
        color = (255, 0, 255, 255)
        out = _draw(
            RegionAnnotation(
                kind="circle",
                bbox=(10, 10, 50, 30),
                color=color,
                fill=(20, 200, 30, 150),
            )
        )
        assert out.getpixel((34, 10)) == color
        assert out.getpixel((34, 24)) != BASE


class TestArrowPrimitive:
    def test_line_and_arrowhead(self) -> None:
        color = (255, 20, 20, 255)
        out = _draw(
            RegionAnnotation(
                kind="arrow",
                start=(5, 50),
                end=(80, 50),
                color=color,
                width=3,
            )
        )
        assert out.getpixel((40, 50)) == color  # shaft
        assert out.getpixel((80, 50)) == color  # tip
        assert out.getpixel((75, 47)) == color  # arrowhead wing

    def test_zero_length_arrow_is_safe_noop(self) -> None:
        canvas = _canvas()
        before = canvas.copy()
        draw_annotations(
            canvas,
            [RegionAnnotation(kind="arrow", start=(10, 10), end=(10, 10))],
            step_index=0,
        )
        assert _difference(before, canvas).getbbox() is None


class TestTextPrimitive:
    def test_rounded_label_at_explicit_point(self) -> None:
        out = _draw(
            RegionAnnotation(
                kind="text",
                start=(5, 5),
                text="Hello",
                color=(255, 255, 255, 255),
                fill=(0, 0, 0, 240),
                padding=4,
                radius=0,
            )
        )
        assert out.getpixel((5, 5)) != BASE
        assert any(
            all(channel > 200 for channel in out.getpixel((x, y))[:3])
            for x in range(5, 50)
            for y in range(5, 30)
        )

    def test_label_near_bbox_uses_default_font(self) -> None:
        before = _canvas()
        after = before.copy()
        draw_annotations(
            after,
            [RegionAnnotation(kind="text", bbox=(20, 45, 30, 15), text="Note")],
            step_index=0,
        )
        diff = _difference(before, after)
        assert diff.getbbox() is not None
        assert diff.getbbox()[3] <= 45  # preferred placement is above the bbox


class TestSpotlightPrimitive:
    def test_dims_only_outside_selected_rectangle(self) -> None:
        out = _draw(RegionAnnotation(kind="spotlight", bbox=(20, 20, 30, 20), dim_alpha=120))
        assert out.getpixel((35, 30)) == BASE
        assert out.getpixel((20, 20)) == BASE
        outside = out.getpixel((5, 5))
        assert outside[3] == 255
        assert all(outside[channel] < BASE[channel] for channel in range(3))

    def test_empty_or_offscreen_cutout_dims_whole_frame(self) -> None:
        for bbox in ((0, 0, 0, 10), (200, 200, 10, 10)):
            out = _draw(RegionAnnotation(kind="spotlight", bbox=bbox, dim_alpha=200))
            assert out.getpixel((50, 40)) != BASE
            assert out.getbbox() == (0, 0, 100, 80)


class TestStepFiltering:
    def test_ranges_are_inclusive_and_unbounded_ranges_continue(self) -> None:
        limited = RegionAnnotation(kind="rect", bbox=(0, 0, 10, 10), start_step=2, end_step=4)
        ongoing = RegionAnnotation(kind="circle", bbox=(0, 0, 5, 5), start_step=3)
        specs = [limited, ongoing]
        assert annotations_for_step(specs, 1) == []
        assert annotations_for_step(specs, 2) == [limited]
        assert annotations_for_step(specs, 4) == [limited, ongoing]
        assert annotations_for_step(specs, 5) == [ongoing]
        assert annotations_for_step(iter(specs), 3) == specs


class TestLoading:
    def test_json_list_and_integral_float_coordinates(self, tmp_path: Path) -> None:
        path = tmp_path / "annotations.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "kind": "rect",
                        "bbox": [1.0, 2.0, 30.0, 20.0],
                        "color": [1, 2, 3, 200],
                        "start_step": 1,
                        "end_step": 3,
                    }
                ]
            ),
            encoding="utf-8",
        )
        specs = load_annotations(path)
        assert specs == [
            RegionAnnotation(
                kind="rect",
                bbox=(1, 2, 30, 20),
                color=(1, 2, 3, 200),
                start_step=1,
                end_step=3,
            )
        ]
        assert isinstance(specs[0].bbox[0], int)

    def test_yaml_mapping_with_annotations(self, tmp_path: Path) -> None:
        path = tmp_path / "annotations.yaml"
        path.write_text(
            """
annotations:
  - kind: arrow
    start: [1, 2]
    end: [30, 40]
  - kind: text
    bbox: [10, 10, 40, 20]
    text: Read me
    fill: [0, 0, 0, 180]
""".strip(),
            encoding="utf-8",
        )
        specs = load_annotations(path)
        assert [spec.kind for spec in specs] == ["arrow", "text"]
        assert specs[0].start == (1, 2)
        assert specs[0].end == (30, 40)
        assert specs[1].fill == (0, 0, 0, 180)

    def test_yaml_top_level_list(self, tmp_path: Path) -> None:
        path = tmp_path / "annotations.yml"
        path.write_text("- kind: circle\n  bbox: [1, 2, 3, 4]\n", encoding="utf-8")
        assert load_annotations(path)[0].bbox == (1, 2, 3, 4)

    @pytest.mark.parametrize(
        ("raw", "match"),
        [
            ({"kind": "rect"}, "requires bbox"),
            ({"kind": "arrow", "start": [0, 0]}, "requires start and end"),
            ({"kind": "text", "bbox": [0, 0, 5, 5]}, "requires text"),
            ({"kind": "text", "text": "hello"}, "requires bbox or start"),
            ({"kind": "rect", "bbox": [0, 0, -1, 5]}, "non-negative"),
            ({"kind": "rect", "bbox": [0.5, 0, 5, 5]}, "integer"),
            ({"kind": "other", "bbox": [0, 0, 5, 5]}, "unknown annotation kind"),
            (
                {"kind": "rect", "bbox": [0, 0, 5, 5], "start_step": 4, "end_step": 2},
                "end_step",
            ),
        ],
    )
    def test_malformed_specs_have_indexed_value_error(
        self, tmp_path: Path, raw: dict[str, object], match: str
    ) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.safe_dump([raw]), encoding="utf-8")
        with pytest.raises(ValueError, match=rf"annotation 0.*{match}"):
            load_annotations(path)

    def test_unknown_keys_are_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(
            "annotations:\n  - kind: rect\n    bbox: [0, 0, 5, 5]\n    surprise: true\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=r"annotation 0.*unknown key.*surprise"):
            load_annotations(path)

    def test_unknown_top_level_keys_are_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("annotations: []\nsurprise: true\n", encoding="utf-8")
        with pytest.raises(ValueError, match=r"top-level.*surprise"):
            load_annotations(path)

    @pytest.mark.parametrize("contents", ["annotations: [", "42", "annotations:\n  - 42"])
    def test_malformed_documents_raise_value_error(self, tmp_path: Path, contents: str) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(contents, encoding="utf-8")
        with pytest.raises(ValueError):
            load_annotations(path)


class TestClippingAndComposition:
    @pytest.mark.parametrize(
        "spec",
        [
            RegionAnnotation(kind="rect", bbox=(-30, -30, 10, 10)),
            RegionAnnotation(kind="circle", bbox=(200, 200, 10, 10)),
            RegionAnnotation(kind="arrow", start=(-20, -20), end=(-5, -5)),
            RegionAnnotation(kind="text", start=(-1, 10), text="offscreen"),
        ],
    )
    def test_wholly_offscreen_non_spotlight_is_noop(self, spec: RegionAnnotation) -> None:
        canvas = _canvas()
        before = canvas.copy()
        draw_annotations(canvas, [spec], step_index=0)
        assert _difference(before, canvas).getbbox() is None

    @pytest.mark.parametrize("kind", ["rect", "circle"])
    def test_partially_offscreen_shapes_are_clipped(self, kind: str) -> None:
        canvas = _canvas()
        before = canvas.copy()
        spec = RegionAnnotation(kind=kind, bbox=(-10, -10, 30, 30))  # type: ignore[arg-type]
        draw_annotations(canvas, [spec], step_index=0)
        assert _difference(before, canvas).getbbox() is not None
        assert canvas.size == before.size

    def test_empty_shapes_and_tiny_text_are_safe(self) -> None:
        canvas = _canvas((2, 2))
        before = canvas.copy()
        specs = [
            RegionAnnotation(kind="rect", bbox=(0, 0, 0, 0)),
            RegionAnnotation(kind="circle", bbox=(0, 0, 1, 0)),
            RegionAnnotation(kind="text", start=(1, 1), text="A much larger label"),
        ]
        draw_annotations(canvas, specs, step_index=0)
        assert canvas.size == (2, 2)
        assert _difference(before, canvas).getbbox() is not None

    def test_inactive_annotations_do_not_change_canvas(self) -> None:
        canvas = _canvas()
        before = canvas.copy()
        draw_annotations(
            canvas,
            [RegionAnnotation(kind="rect", bbox=(0, 0, 20, 20), start_step=2)],
            step_index=1,
        )
        assert _difference(before, canvas).getbbox() is None

    def test_rgba_output_is_in_place_and_deterministic(self) -> None:
        specs = [
            RegionAnnotation(kind="spotlight", bbox=(10, 10, 50, 40)),
            RegionAnnotation(kind="rect", bbox=(10, 10, 50, 40), fill=(0, 255, 0, 50)),
            RegionAnnotation(kind="arrow", start=(0, 60), end=(80, 20)),
            RegionAnnotation(kind="text", bbox=(10, 10, 50, 40), text="Demo"),
        ]
        first = draw_annotations(_canvas(), specs, step_index=0, phase=0.25)
        second = draw_annotations(_canvas(), specs, step_index=0, phase=0.25)
        assert first.mode == "RGBA"
        assert first.tobytes() == second.tobytes()
