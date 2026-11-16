"""End-to-end smooth annotated MP4 pipeline (no browser required)."""

from __future__ import annotations

import json
from pathlib import Path

import imageio.v2 as iio
import pytest
from PIL import Image

from clickcast.annotate.annotator import AnnotateConfig
from clickcast.annotate.pipeline import StepAnnotation, annotate_frames_dir
from clickcast.annotate.primitives import RegionAnnotation
from clickcast.annotate.smooth import SmoothConfig, smooth_frames_dir
from clickcast.encode import encode


def test_smooth_annotated_mp4_preserves_duration_and_output_fps(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    entries = []
    colors = [(235, 240, 248), (235, 240, 248), (40, 80, 130), (40, 80, 130)]
    for index, color in enumerate(colors):
        name = f"frame-{index:04d}.png"
        Image.new("RGB", (96, 64), color).save(frames_dir / name)
        entries.append(
            {
                "path": name,
                "step_index": index // 2,
                "sub_index": index % 2,
                "cursor_xy": [8, 32] if index < 2 else [80, 32],
            }
        )
    (frames_dir / "frames.json").write_text(json.dumps({"frames": entries}))

    smoothed = smooth_frames_dir(
        frames_dir,
        SmoothConfig(output_fps=12, transition_ms=80),
        source_fps=2,
    )
    assert smoothed.total_frames == 24
    assert smoothed.total_frames / smoothed.output_fps == pytest.approx(2.0)

    annotated = annotate_frames_dir(
        frames_dir,
        steps={
            0: StepAnnotation(label="Move to the action", click_at=(8, 32)),
            1: StepAnnotation(label="Inspect the result", click_at=(80, 32)),
        },
        config=AnnotateConfig(actions_panel=False, labels=False, progress=False),
        regions=[
            RegionAnnotation(kind="spotlight", bbox=(60, 12, 30, 40), start_step=1),
            RegionAnnotation(kind="rect", bbox=(62, 14, 26, 36), start_step=1),
            RegionAnnotation(kind="arrow", start=(8, 50), end=(75, 50), start_step=0),
            RegionAnnotation(kind="text", start=(8, 4), text="Action", start_step=0),
        ],
    )
    assert annotated == 24

    result = encode(frames_dir, tmp_path / "tour.mp4", fps=smoothed.output_fps)
    assert result.frame_count == 24
    assert result.duration_s == pytest.approx(2.0)
    assert result.size_bytes > 100

    with iio.get_reader(result.path) as reader:
        assert reader.get_meta_data()["fps"] == pytest.approx(12)
        assert reader.count_frames() == 24
        # Decode both ends to prove this is a real, readable H.264 animation,
        # not merely a file with an .mp4 suffix.
        assert reader.get_data(0).shape == (64, 96, 3)
        assert reader.get_data(23).shape == (64, 96, 3)
