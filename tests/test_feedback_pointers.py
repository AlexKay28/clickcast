"""Tests for the sidecar's optional ``feedback`` pointer block."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from clickcast.feedback import Media, Report, build_feedback, load, write
from clickcast.feedback.pointers import (
    DIAGNOSTICS_COMMAND,
    DOCS_URL,
    REPO_URL,
    REPORT_URL,
    SCHEMA_URL,
    feedback_pointer_lines,
)


def _report(**overrides: object) -> Report:
    base: dict[str, object] = {
        "clickcast_version": "1.2.3",
        "url": "https://example.com/app",
        "engine": "chromium",
        "viewport": [1440, 900],
        "started_at": "2026-07-26T10:00:00Z",
        "duration_s": 3.5,
        "media": Media(
            path="/tmp/reel.gif",
            format="gif",
            size_bytes=1024,
            frame_count=10,
            duration_s=1.0,
            fps=10,
        ),
    }
    base.update(overrides)
    return Report(**base)  # type: ignore[arg-type]


class TestBuildFeedback:
    def test_block_has_all_required_fields(self) -> None:
        fb = build_feedback(_report())
        assert fb.message
        assert fb.repo == REPO_URL
        assert fb.issues_url == f"{REPO_URL}/issues"
        assert fb.new_issue_url.startswith(f"{REPO_URL}/issues/new?")
        assert fb.template.problem
        assert fb.template.resolution_plan

    def test_block_carries_issue40_pointers(self) -> None:
        fb = build_feedback(_report())
        assert fb.report_url == REPORT_URL
        assert fb.schema_url == SCHEMA_URL
        assert fb.docs_url == DOCS_URL
        assert fb.diagnostics_command == DIAGNOSTICS_COMMAND

    def test_pointer_lines_reference_all_four_urls(self) -> None:
        joined = "\n".join(feedback_pointer_lines())
        assert REPORT_URL in joined
        assert SCHEMA_URL in joined
        assert DOCS_URL in joined
        assert DIAGNOSTICS_COMMAND in joined

    def test_new_issue_url_prefills_environment(self) -> None:
        fb = build_feedback(_report())
        query = parse_qs(urlparse(fb.new_issue_url).query)
        body = query["body"][0]
        assert "clickcast: 1.2.3" in body
        assert "engine: chromium" in body
        assert "viewport: 1440x900" in body
        assert "target url: https://example.com/app" in body

    def test_body_omits_target_url_when_absent(self) -> None:
        fb = build_feedback(_report(url=None))
        body = parse_qs(urlparse(fb.new_issue_url).query)["body"][0]
        assert "target url" not in body

    def test_body_includes_prompt_template_labels(self) -> None:
        fb = build_feedback(_report())
        body = parse_qs(urlparse(fb.new_issue_url).query)["body"][0]
        assert "**Problem**" in body
        assert "**Resolution plan**" in body


class TestWrite:
    def test_write_without_feedback_omits_block(self, tmp_path: Path) -> None:
        out = write(_report(), tmp_path / "r.json")
        data = json.loads(out.read_text())
        assert data.get("feedback") is None

    def test_write_with_feedback_attaches_block(self, tmp_path: Path) -> None:
        out = write(_report(), tmp_path / "r.json", with_feedback=True)
        data = json.loads(out.read_text())
        assert data["feedback"] is not None
        assert data["feedback"]["repo"] == REPO_URL
        assert "new_issue_url" in data["feedback"]

    def test_write_does_not_mutate_input_report(self, tmp_path: Path) -> None:
        report = _report()
        write(report, tmp_path / "r.json", with_feedback=True)
        assert report.feedback is None, "input report should be untouched"

    def test_preexisting_feedback_survives_write(self, tmp_path: Path) -> None:
        # Caller can build their own Feedback and set it; write should not overwrite.
        report = _report()
        custom = build_feedback(report).model_copy(update={"message": "custom!"})
        report_with = report.model_copy(update={"feedback": custom})
        out = write(report_with, tmp_path / "r.json", with_feedback=True)
        data = json.loads(out.read_text())
        assert data["feedback"]["message"] == "custom!"

    def test_written_file_round_trips_through_load(self, tmp_path: Path) -> None:
        out = write(_report(), tmp_path / "r.json", with_feedback=True)
        loaded = load(out)
        assert loaded.feedback is not None
        assert loaded.feedback.repo == REPO_URL


@pytest.mark.parametrize("with_feedback", [True, False])
def test_schema_still_validates(tmp_path: Path, with_feedback: bool) -> None:
    out = write(_report(), tmp_path / "r.json", with_feedback=with_feedback)
    Report.model_validate_json(out.read_text())
