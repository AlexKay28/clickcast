"""Tests for :mod:`clickcast.feedback.visual_diff` and the ``clickcast diff`` CLI.

Mirrors the structure of ``tests/test_feedback_assertions.py`` (#112): model
shape tests, then behavioral tests through the public :func:`visual_diff`
surface, then the CLI. No browser / Playwright needed anywhere in this file
— every fixture is a synthetic PNG pair built directly with Pillow, per the
brief in #205 ("a blank image vs. one with a shifted rectangle").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError
from typer.testing import CliRunner

from clickcast.cli import app
from clickcast.feedback import write
from clickcast.feedback.models import (
    BBox,
    Media,
    PageState,
    Report,
    StepReport,
    UnmatchedStep,
    VisualDiffReport,
)
from clickcast.feedback.visual_diff import (
    DEFAULT_THRESHOLD,
    VISUAL_DIFF_SCHEMA_VERSION,
    _label_bbox,
    _panel_bbox,
    _progress_bbox,
    load_visual_diff_report,
    max_changed_pct,
    visual_diff,
)
from clickcast.reel import Reel

# Real default viewport size (see Report.viewport's default [1280, 800]) so
# the default overlay-exclusion geometry (tuned for that scale) behaves
# realistically — a much smaller canvas would make the default corner/band
# boxes swallow the whole frame.
FRAME_SIZE = (1280, 800)

# A content rectangle safely outside every default overlay-exclusion zone
# (progress bar bottom strip, bottom label band, top-right actions panel).
SAFE_RECT_A = (200, 300, 400, 420)
SAFE_RECT_B = (200, 300, 350, 380)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _media(fmt: str = "gif", path: str = "tour.gif") -> Media:
    return Media(path=path, format=fmt, size_bytes=1024, frame_count=1, duration_s=1.0, fps=12)


def _draw_frame(
    path: Path,
    *,
    size: tuple[int, int] = FRAME_SIZE,
    rect: tuple[int, int, int, int] | None = None,
    rect_color: tuple[int, int, int] = (200, 30, 30),
) -> None:
    im = Image.new("RGB", size, (255, 255, 255))
    if rect is not None:
        ImageDraw.Draw(im).rectangle(rect, fill=rect_color)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def _write_sidecar(
    tmp_path: Path,
    subdir: str,
    *,
    steps: list[dict[str, object]],
    fmt: str = "gif",
) -> Path:
    """Build a Report with the given step specs and write frames + sidecar.

    Each step dict: ``label``, ``frame`` (filename, ``""`` for "no frame
    captured"), ``rect`` (or None), ``cursor_xy`` (optional), ``size``
    (optional, defaults to FRAME_SIZE).
    """
    out_dir = tmp_path / subdir
    step_reports = []
    for i, spec in enumerate(steps):
        frame_name = spec["frame"]
        assert isinstance(frame_name, str)
        if frame_name:
            _draw_frame(
                out_dir / frame_name,
                size=spec.get("size", FRAME_SIZE),  # type: ignore[arg-type]
                rect=spec.get("rect"),  # type: ignore[arg-type]
            )
        step_reports.append(
            StepReport(
                index=i,
                action="click",
                args={},
                status="ok",
                duration_ms=10.0,
                frames=[frame_name] if frame_name else [],
                label=spec.get("label"),  # type: ignore[arg-type]
                cursor_xy=spec.get("cursor_xy"),  # type: ignore[arg-type]
                page_state=PageState(),
            )
        )
    report = Report(
        clickcast_version="0.1.0",
        started_at="2026-01-01T00:00:00+00:00",
        duration_s=1.0,
        media=_media(fmt=fmt),
        steps=step_reports,
    )
    return write(report, out_dir / "reel.gif.json")


# ------------------------------------------------------------------
# Model shape
# ------------------------------------------------------------------


class TestModel:
    def test_bbox_requires_positive_dims(self) -> None:
        with pytest.raises(ValidationError):
            BBox(x=0, y=0, width=0, height=10)

    def test_extra_forbid_on_report(self) -> None:
        with pytest.raises(ValidationError):
            VisualDiffReport.model_validate(
                {
                    "schema_version": 1,
                    "threshold": 24.0,
                    "exclude_overlays": True,
                    "steps": [],
                    "unmatched_steps": [],
                    "surprise": True,
                }
            )

    def test_unmatched_step_side_literal(self) -> None:
        with pytest.raises(ValidationError):
            UnmatchedStep(side="neither", index=0, reason="x")  # type: ignore[arg-type]

    def test_round_trips_through_json(self, tmp_path: Path) -> None:
        report = VisualDiffReport(
            schema_version=VISUAL_DIFF_SCHEMA_VERSION,
            threshold=24.0,
            exclude_overlays=True,
            steps=[],
            unmatched_steps=[UnmatchedStep(side="run", index=0, reason="no baseline counterpart")],
        )
        path = tmp_path / "summary.json"
        path.write_text(json.dumps(report.model_dump(mode="json"), indent=2))
        loaded = load_visual_diff_report(path)
        assert loaded == report


# ------------------------------------------------------------------
# visual_diff() — pairing
# ------------------------------------------------------------------


class TestPairing:
    def test_equal_counts_pair_by_index(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[
                {"label": "Open", "frame": "f0.png", "rect": None},
                {"label": "Click", "frame": "f1.png", "rect": None},
            ],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[
                {"label": "Open", "frame": "f0.png", "rect": None},
                {"label": "Click", "frame": "f1.png", "rect": None},
            ],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        assert report.unmatched_steps == []
        assert [s.run_index for s in report.steps] == [0, 1]
        assert [s.baseline_index for s in report.steps] == [0, 1]

    def test_mismatched_counts_fall_back_to_label(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[
                {"label": "Open", "frame": "f0.png", "rect": None},
                {"label": "Extra step", "frame": "f1.png", "rect": None},
                {"label": "Click", "frame": "f2.png", "rect": None},
            ],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[
                {"label": "Open", "frame": "f0.png", "rect": None},
                {"label": "Click", "frame": "f1.png", "rect": None},
            ],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        paired_labels = {s.label for s in report.steps}
        assert paired_labels == {"Open", "Click"}
        assert len(report.unmatched_steps) == 1
        unmatched = report.unmatched_steps[0]
        assert unmatched.side == "run"
        assert unmatched.label == "Extra step"
        assert unmatched.reason == "no baseline counterpart"

    def test_baseline_only_step_flagged_no_run_counterpart(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[
                {"label": "Open", "frame": "f0.png", "rect": None},
                {"label": "Removed step", "frame": "f1.png", "rect": None},
            ],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        assert len(report.unmatched_steps) == 1
        unmatched = report.unmatched_steps[0]
        assert unmatched.side == "baseline"
        assert unmatched.label == "Removed step"
        assert unmatched.reason == "no run counterpart"

    def test_missing_frame_flagged_not_crashed(self, tmp_path: Path) -> None:
        # Same step count so pairing is index-based, but the run step has no
        # frames recorded at all — visual_diff must flag it, not crash.
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "", "rect": None}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        assert report.steps == []
        assert len(report.unmatched_steps) == 1
        assert report.unmatched_steps[0].reason == "missing frame"
        assert report.unmatched_steps[0].side == "run"

    def test_frame_file_absent_from_disk_flagged(self, tmp_path: Path) -> None:
        # frames=[...] names a file, but nothing was ever written there —
        # simulates a sidecar whose temp frame dir was cleaned up.
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        (tmp_path / "run" / "f0.png").unlink()
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        assert report.steps == []
        assert report.unmatched_steps[0].reason == "missing frame"
        assert report.unmatched_steps[0].side == "run"


# ------------------------------------------------------------------
# visual_diff() — pixel diff + threshold + regions
# ------------------------------------------------------------------


class TestPixelDiff:
    def test_identical_frames_report_zero_pct(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        assert report.steps[0].changed_pct == 0.0
        assert report.steps[0].regions == []
        assert report.steps[0].diff_image_path is None

    def test_injected_change_reports_bounded_region(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_B}],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        step = report.steps[0]
        assert step.changed_pct > 0.0
        assert len(step.regions) == 1
        region = step.regions[0]
        # SAFE_RECT_A - SAFE_RECT_B is the extra strip the run frame drew:
        # x 200-400, y 300-420 minus x 200-350, y 300-380 → bounded roughly
        # around that L-shaped delta, well inside the two rects' envelope.
        assert 150 <= region.x <= 210
        assert 290 <= region.y <= 310
        assert region.x + region.width <= 410
        assert region.y + region.height <= 430
        assert step.diff_image_path is not None
        assert Path(step.diff_image_path).exists()

    def test_threshold_suppresses_subtle_noise(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        # A subtle uniform color shift (delta 10 on one channel) across the
        # whole frame — smaller than DEFAULT_THRESHOLD, should read clean.
        im = Image.new("RGB", FRAME_SIZE, (245, 255, 255))
        (base_dir).mkdir(exist_ok=True)
        im.save(base_dir / "f0.png")
        report_ = Report(
            clickcast_version="0.1.0",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=1.0,
            media=_media(),
            steps=[
                StepReport(
                    index=0,
                    action="click",
                    args={},
                    status="ok",
                    duration_ms=1.0,
                    frames=["f0.png"],
                    label="Open",
                    page_state=PageState(),
                )
            ],
        )
        base = write(report_, base_dir / "reel.gif.json")

        clean = visual_diff(
            run, base, out_dir=tmp_path / "out_default", threshold=DEFAULT_THRESHOLD
        )
        assert clean.steps[0].changed_pct == 0.0

        sensitive = visual_diff(run, base, out_dir=tmp_path / "out_low", threshold=2.0)
        assert sensitive.steps[0].changed_pct > 0.0

    def test_dimension_mismatch_reports_full_frame_change(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None, "size": (640, 480)}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None, "size": (1280, 800)}],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        assert report.steps[0].changed_pct == 100.0
        assert report.steps[0].regions == [BBox(x=0, y=0, width=640, height=480)]

    def test_max_changed_pct_helper(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[
                {"label": "a", "frame": "f0.png", "rect": SAFE_RECT_A},
                {"label": "b", "frame": "f1.png", "rect": None},
            ],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[
                {"label": "a", "frame": "f0.png", "rect": SAFE_RECT_B},
                {"label": "b", "frame": "f1.png", "rect": None},
            ],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        assert max_changed_pct(report) == report.steps[0].changed_pct
        assert max_changed_pct(report) > 0.0


# ------------------------------------------------------------------
# visual_diff() — overlay exclusion (#202 acceptance criterion)
# ------------------------------------------------------------------


class TestOverlayExclusion:
    def test_progress_and_label_band_diff_excluded_by_default(self, tmp_path: Path) -> None:
        w, h = FRAME_SIZE
        progress = _progress_bbox(w, h)
        label = _label_bbox(w, h)
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        im = Image.new("RGB", FRAME_SIZE, (255, 255, 255))
        draw = ImageDraw.Draw(im)
        # Rects strictly INSIDE the computed exclusion bands (not the gap
        # between them) — this is what an annotator's own progress bar /
        # label bar would actually paint over.
        draw.rectangle(
            (progress.x, progress.y, progress.x + progress.width, progress.y + progress.height),
            fill=(200, 30, 30),
        )
        draw.rectangle(
            (label.x, label.y, label.x + label.width, label.y + label.height),
            fill=(30, 30, 200),
        )
        im.save(run_dir / "f0.png")
        report = Report(
            clickcast_version="0.1.0",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=1.0,
            media=_media(),
            steps=[
                StepReport(
                    index=0,
                    action="click",
                    args={},
                    status="ok",
                    duration_ms=1.0,
                    frames=["f0.png"],
                    label="Open",
                    page_state=PageState(),
                )
            ],
        )
        run = write(report, run_dir / "reel.gif.json")

        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        excluded = visual_diff(run, base, out_dir=tmp_path / "out_excl")
        assert excluded.steps[0].changed_pct == pytest.approx(0.0, abs=0.01)

        raw = visual_diff(run, base, out_dir=tmp_path / "out_raw", exclude_overlays=False)
        assert raw.steps[0].changed_pct > excluded.steps[0].changed_pct
        assert raw.steps[0].changed_pct > 0.0

    def test_actions_panel_diff_excluded_by_default(self, tmp_path: Path) -> None:
        w, h = FRAME_SIZE
        panel = _panel_bbox(w, h)
        rect = (panel.x, panel.y, panel.x + panel.width, panel.y + panel.height)
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": rect}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        excluded = visual_diff(run, base, out_dir=tmp_path / "out_excl")
        assert excluded.steps[0].changed_pct == pytest.approx(0.0, abs=0.01)

        raw = visual_diff(run, base, out_dir=tmp_path / "out_raw", exclude_overlays=False)
        assert raw.steps[0].changed_pct > 0.0

    def test_cursor_diff_excluded_by_default(self, tmp_path: Path) -> None:
        cursor = [640, 400]
        rect = (cursor[0] - 40, cursor[1] - 40, cursor[0] + 40, cursor[1] + 40)
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": rect, "cursor_xy": cursor}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None, "cursor_xy": cursor}],
        )
        excluded = visual_diff(run, base, out_dir=tmp_path / "out_excl")
        assert excluded.steps[0].changed_pct == pytest.approx(0.0, abs=0.01)

        raw = visual_diff(run, base, out_dir=tmp_path / "out_raw", exclude_overlays=False)
        assert raw.steps[0].changed_pct > 0.0

    def test_content_change_outside_overlays_still_detected_with_exclusion_on(
        self, tmp_path: Path
    ) -> None:
        # Sanity check that turning exclusion ON doesn't hide EVERYTHING —
        # a real content change well outside every overlay zone must still
        # show up.
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        report = visual_diff(run, base, out_dir=tmp_path / "out")
        assert report.steps[0].changed_pct > 0.0
        assert report.steps[0].regions


# ------------------------------------------------------------------
# visual_diff() — output files
# ------------------------------------------------------------------


class TestOutputFiles:
    def test_default_out_dir_derived_from_run_sidecar(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_B}],
        )
        visual_diff(run, base)
        expected = run.parent / "reel.diff"
        assert expected.exists()
        assert (expected / "summary.json").exists()

    def test_summary_json_round_trips(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_B}],
        )
        out_dir = tmp_path / "out"
        report = visual_diff(run, base, out_dir=out_dir)
        loaded = load_visual_diff_report(out_dir / "summary.json")
        assert loaded == report


# ------------------------------------------------------------------
# Reel.visual_diff()
# ------------------------------------------------------------------


class TestReelIntegration:
    def test_reel_visual_diff_delegates(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_B}],
        )
        reel = Reel("https://x")
        report = reel.visual_diff(run, base, out_dir=tmp_path / "out")
        assert isinstance(report, VisualDiffReport)
        assert report.steps[0].changed_pct > 0.0


# ------------------------------------------------------------------
# CLI — `clickcast diff`
# ------------------------------------------------------------------


class TestCLI:
    runner = CliRunner()

    def test_default_reports_and_exits_zero(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_B}],
        )
        result = self.runner.invoke(
            app, ["diff", str(run), str(base), "--out", str(tmp_path / "o")]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "o" / "summary.json").exists()

    def test_fail_above_exceeded_exits_nonzero(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_B}],
        )
        result = self.runner.invoke(
            app,
            ["diff", str(run), str(base), "--out", str(tmp_path / "o"), "--fail-above", "0.01"],
        )
        assert result.exit_code == 1, result.output

    def test_fail_above_not_exceeded_exits_zero(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": SAFE_RECT_A}],
        )
        result = self.runner.invoke(
            app,
            ["diff", str(run), str(base), "--out", str(tmp_path / "o"), "--fail-above", "5"],
        )
        assert result.exit_code == 0, result.output

    def test_unmatched_step_fails_even_under_generous_gate(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[
                {"label": "Open", "frame": "f0.png", "rect": None},
                {"label": "Removed", "frame": "f1.png", "rect": None},
            ],
        )
        result = self.runner.invoke(
            app,
            ["diff", str(run), str(base), "--out", str(tmp_path / "o"), "--fail-above", "100"],
        )
        assert result.exit_code == 1, result.output
        assert "unmatched" in result.output

    def test_no_exclude_overlays_flag(self, tmp_path: Path) -> None:
        cursor = [640, 400]
        rect = (cursor[0] - 40, cursor[1] - 40, cursor[0] + 40, cursor[1] + 40)
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": rect, "cursor_xy": cursor}],
        )
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None, "cursor_xy": cursor}],
        )
        result = self.runner.invoke(
            app,
            [
                "diff",
                str(run),
                str(base),
                "--out",
                str(tmp_path / "o"),
                "--no-exclude-overlays",
                "--fail-above",
                "0.01",
            ],
        )
        assert result.exit_code == 1, result.output

    def test_missing_run_sidecar_errors(self, tmp_path: Path) -> None:
        base = _write_sidecar(
            tmp_path,
            "base",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        result = self.runner.invoke(app, ["diff", str(tmp_path / "nope.json"), str(base)])
        assert result.exit_code != 0
        assert "sidecar not found" in result.output

    def test_missing_baseline_sidecar_errors(self, tmp_path: Path) -> None:
        run = _write_sidecar(
            tmp_path,
            "run",
            steps=[{"label": "Open", "frame": "f0.png", "rect": None}],
        )
        result = self.runner.invoke(app, ["diff", str(run), str(tmp_path / "nope.json")])
        assert result.exit_code != 0
        assert "baseline not found" in result.output

    def test_help_lists_contract_flags(self) -> None:
        result = self.runner.invoke(app, ["diff", "--help"])
        assert result.exit_code == 0
        for flag in ("--out", "--threshold", "--no-exclude-overlays", "--fail-above"):
            assert flag in result.output
