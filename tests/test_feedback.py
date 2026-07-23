from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from clickcast.feedback import (
    DiscoveredElement,
    Media,
    PageState,
    Report,
    ReportBuilder,
    StepReport,
    load,
    write,
)

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "src" / "clickcast" / "feedback" / "schema" / "v1.json"


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


def _valid_media() -> Media:
    return Media(
        path="tour.gif",
        format="gif",
        size_bytes=1024,
        frame_count=12,
        duration_s=1.0,
        fps=12,
    )


def _valid_report() -> Report:
    return Report(
        clickcast_version="0.1.0",
        started_at="2026-07-23T15:00:00+00:00",
        duration_s=5.5,
        media=_valid_media(),
        steps=[
            StepReport(
                index=0,
                action="goto",
                args={"url": "https://x"},
                status="ok",
                duration_ms=1200.0,
                frames=["frame-0000-000.png"],
            )
        ],
    )


class TestModels:
    def test_media_positive_size(self) -> None:
        with pytest.raises(ValidationError):
            Media(path="x", format="gif", size_bytes=-1, frame_count=1, duration_s=0.1, fps=1)

    def test_page_state_caps_lists_at_50(self) -> None:
        with pytest.raises(ValidationError):
            PageState(console_errors=["e"] * 51)

    def test_discovered_element_bbox_needs_4(self) -> None:
        with pytest.raises(ValidationError):
            DiscoveredElement(selector="s", role="r", text="t", bbox=[0, 0, 1], score=1, source="x")

    def test_step_report_requires_status(self) -> None:
        with pytest.raises(ValidationError):
            StepReport(index=0, action="goto", duration_ms=1.0)  # type: ignore[call-arg]

    def test_report_default_schema_version_is_1(self) -> None:
        assert _valid_report().schema_version == 1

    def test_report_defaults_are_forward_compatible(self) -> None:
        # Roadmap #29 Track C adds a top-level `graph` block. The base model
        # must accept unknown top-level keys silently so a v2 file can round-
        # trip through a v1 parser without exploding on strict-extras.
        payload = _valid_report().model_dump()
        payload["graph"] = {"nodes": [], "edges": []}
        # Should NOT raise
        Report.model_validate(payload)


# ------------------------------------------------------------------
# JSON Schema — model_json_schema() must match the committed file
# ------------------------------------------------------------------


class TestJsonSchema:
    def test_committed_schema_matches_model(self) -> None:
        emitted = Report.model_json_schema()
        assert SCHEMA_PATH.exists(), (
            "committed schema missing — run `python scripts/gen_feedback_schema.py`"
        )
        committed = json.loads(SCHEMA_PATH.read_text())
        assert emitted == committed, (
            "committed schema is stale — run `python scripts/gen_feedback_schema.py` "
            "and commit the update"
        )

    def test_schema_advertises_v1(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text())
        # schema_version has default 1 in the model — check the default made it
        assert schema["properties"]["schema_version"]["default"] == 1


# ------------------------------------------------------------------
# Round-trip + load()/write()
# ------------------------------------------------------------------


class TestRoundTrip:
    def test_write_then_load_returns_equal_report(self, tmp_path: Path) -> None:
        original = _valid_report()
        path = write(original, tmp_path / "tour.gif.json")
        loaded = load(path)
        assert loaded == original

    def test_serialization_is_json(self, tmp_path: Path) -> None:
        path = write(_valid_report(), tmp_path / "tour.gif.json")
        # Must be valid JSON with predictable indentation
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == 1
        assert payload["media"]["format"] == "gif"

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "does-not-exist.json")


# ------------------------------------------------------------------
# Builder unit — no browser
# ------------------------------------------------------------------


class TestBuilder:
    def test_finalises_without_attach(self) -> None:
        # A builder can be built even if no session was ever attached — used
        # for tests + dry runs where we still want media metadata.
        builder = ReportBuilder(url="https://x", engine="chromium", viewport=(400, 300))
        report = builder.build(_valid_media())
        assert report.url == "https://x"
        assert report.viewport == [400, 300]
        assert report.discovered_elements == []
        assert report.steps == []

    def test_add_warning_and_error_propagate(self) -> None:
        builder = ReportBuilder(engine="chromium")
        builder.add_warning("hydration was slow")
        builder.add_error("goto returned 500")
        report = builder.build(_valid_media())
        assert report.warnings == ["hydration was slow"]
        assert report.errors == ["goto returned 500"]


# ------------------------------------------------------------------
# Consumer example — the tests/consumer/read_sidecar.py script
# ------------------------------------------------------------------


class TestConsumerExample:
    def test_consumer_lists_failed_step_frames(self, tmp_path: Path) -> None:
        # Build a report with one ok + one failed step, write it, then
        # invoke the consumer script as a subprocess — proves the sidecar is
        # usable from outside the package without importing it.
        report = Report(
            clickcast_version="0.1.0",
            started_at="2026-07-23T15:00:00+00:00",
            duration_s=1.0,
            media=_valid_media(),
            steps=[
                StepReport(
                    index=0,
                    action="goto",
                    args={"url": "https://x"},
                    status="ok",
                    duration_ms=100.0,
                    frames=["frame-0000-000.png"],
                ),
                StepReport(
                    index=1,
                    action="click",
                    args={"selector": "#gone"},
                    status="failed",
                    duration_ms=250.0,
                    frames=["frame-0001-000.png", "frame-0001-001.png"],
                    error="TimeoutError: locator not found",
                ),
            ],
        )
        sidecar = write(report, tmp_path / "tour.gif.json")
        script = REPO_ROOT / "tests" / "consumer" / "read_sidecar.py"
        result = subprocess.run(
            [sys.executable, str(script), str(sidecar)],
            capture_output=True,
            text=True,
            check=True,
        )
        # Consumer prints: "<index> <action> -> <frames_csv>" per failed step
        assert "1 click" in result.stdout
        assert "frame-0001-000.png,frame-0001-001.png" in result.stdout
