"""Tests for :mod:`clickcast.feedback.report_bug` and the ``clickcast report-bug`` CLI."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from typer.testing import CliRunner

from clickcast.cli import app
from clickcast.feedback import Media, Report, StepReport, write
from clickcast.feedback.report_bug import (
    build_agent_report,
    prefilled_issue_url,
    render_diagnostics,
)


def _step(**kw: object) -> StepReport:
    base = {"index": 0, "action": "click", "status": "ok", "duration_ms": 100.0}
    base.update(kw)
    return StepReport(**base)  # type: ignore[arg-type]


def _report(**overrides: object) -> Report:
    base: dict[str, object] = {
        "clickcast_version": "1.2.3",
        "url": "https://acme.example/app",
        "engine": "chromium",
        "viewport": [1440, 900],
        "started_at": "2026-07-26T10:00:00Z",
        "duration_s": 4.2,
        "media": Media(
            path="/tmp/reel.gif",
            format="gif",
            size_bytes=1024,
            frame_count=10,
            duration_s=1.0,
            fps=10,
        ),
        "steps": [_step()],
    }
    base.update(overrides)
    return Report(**base)  # type: ignore[arg-type]


class TestPayloadShape:
    def test_required_fields_present(self) -> None:
        payload = build_agent_report(_report())
        for k in (
            "schema_version",
            "clickcast_version",
            "python",
            "os",
            "command_or_api_call",
            "expected",
            "actual",
            "reproduction",
            "sidecar_excerpt",
            "redacted",
        ):
            assert k in payload, f"missing key {k}"
        assert payload["schema_version"] == 1

    def test_reproduction_is_cli(self) -> None:
        payload = build_agent_report(_report())
        assert payload["reproduction"]["kind"] == "cli"
        assert payload["reproduction"]["content"].startswith("clickcast auto ")

    def test_failed_step_narrated(self) -> None:
        failed = _step(index=2, action="click", status="failed", error="timeout after 5000ms")
        payload = build_agent_report(_report(steps=[_step(), failed]))
        assert "step 2" in payload["expected"].lower()
        assert "timeout" in payload["actual"].lower()
        assert payload["sidecar_excerpt"]["failed_step"]["index"] == 2

    def test_happy_path_narrated(self) -> None:
        payload = build_agent_report(_report())
        assert "GIF" in payload["expected"]
        assert payload["sidecar_excerpt"].get("failed_step") is None

    def test_environment_note_threaded(self) -> None:
        payload = build_agent_report(_report(), environment_note="proxy on")
        assert payload["environment_note"] == "proxy on"


class TestRedactionIntegration:
    def test_default_redacted_true(self) -> None:
        payload = build_agent_report(_report())
        assert payload["redacted"] is True

    def test_url_sanitized_when_redacted(self) -> None:
        payload = build_agent_report(_report())
        assert "example" in payload["sidecar_excerpt"]["url"]
        # Original .example TLD stays; hostname gets its TLD replaced with `.example`.
        assert "acme.example" in payload["sidecar_excerpt"]["url"]

    def test_no_redact_preserves_urls(self) -> None:
        payload = build_agent_report(_report(), redact=False)
        assert payload["sidecar_excerpt"]["url"] == "https://acme.example/app"
        assert payload["redacted"] is False


class TestIssueURL:
    def test_url_has_template_reference(self) -> None:
        payload = build_agent_report(_report())
        url = prefilled_issue_url(payload)
        q = parse_qs(urlparse(url).query)
        assert q["template"][0] == "ai-agent-report.yml"
        assert q["title"][0].startswith("[agent-report]")
        assert "clickcast" in q["body"][0]

    def test_url_body_includes_excerpt_json(self) -> None:
        failed = _step(index=1, action="click", status="failed", error="not found")
        payload = build_agent_report(_report(steps=[failed]))
        url = prefilled_issue_url(payload)
        body = parse_qs(urlparse(url).query)["body"][0]
        assert "not found" in body
        assert "```json" in body


class TestDiagnostics:
    def test_renders_command_and_narrative(self) -> None:
        text = render_diagnostics(build_agent_report(_report()))
        assert "clickcast 1.2.3" in text
        assert "command:" in text
        assert "expected:" in text
        assert "actual:" in text

    def test_shows_failed_step_line(self) -> None:
        failed = _step(index=1, action="click", status="failed", error="oops")
        text = render_diagnostics(build_agent_report(_report(steps=[failed])))
        assert "failed step #1" in text
        assert "oops" in text


class TestCLI:
    runner = CliRunner()

    def _sidecar(self, tmp_path: Path, **overrides: object) -> Path:
        path = tmp_path / "reel.gif.json"
        return write(_report(**overrides), path)

    def test_missing_sidecar_exits_nonzero(self, tmp_path: Path) -> None:
        result = self.runner.invoke(app, ["report-bug", str(tmp_path / "missing.json")])
        assert result.exit_code != 0

    def test_prose_output_contains_url(self, tmp_path: Path) -> None:
        sc = self._sidecar(tmp_path)
        result = self.runner.invoke(app, ["report-bug", str(sc)])
        assert result.exit_code == 0
        assert "clickcast 1.2.3" in result.stdout
        assert "issues/new" in result.stdout

    def test_json_flag_emits_track_c_payload(self, tmp_path: Path) -> None:
        sc = self._sidecar(tmp_path)
        result = self.runner.invoke(app, ["report-bug", str(sc), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["schema_version"] == 1
        assert "issue_url" in payload
        assert "sidecar_excerpt" in payload

    def test_no_redact_leaves_original_url(self, tmp_path: Path) -> None:
        sc = self._sidecar(tmp_path)
        result = self.runner.invoke(app, ["report-bug", str(sc), "--json", "--no-redact"])
        payload = json.loads(result.stdout)
        assert payload["sidecar_excerpt"]["url"] == "https://acme.example/app"
        assert payload["redacted"] is False

    def test_note_threaded_through(self, tmp_path: Path) -> None:
        sc = self._sidecar(tmp_path)
        result = self.runner.invoke(app, ["report-bug", str(sc), "--json", "--note", "proxy on"])
        payload = json.loads(result.stdout)
        assert payload["environment_note"] == "proxy on"


@pytest.mark.parametrize("cmd", ["auto", "run", "report-bug", "doctor"])
def test_help_epilog_carries_feedback_pointers(cmd: str) -> None:
    from clickcast.feedback.pointers import REPORT_URL

    runner = CliRunner()
    result = runner.invoke(app, [cmd, "--help"])
    assert result.exit_code == 0, result.stdout
    assert REPORT_URL in result.stdout
