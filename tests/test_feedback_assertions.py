"""Tests for :mod:`clickcast.feedback.assertions` and the ``clickcast assertions`` CLI.

Two things this suite protects:

1. The distilled shape stays byte-identical across the timestamp / frame-path
   / URL-query-string variations that make raw sidecar diffs useless for CI.
2. The diff surface is stable and human-legible for the CI regression gate
   sold in the README (equal / step-added / step-status-changed / console-
   error-count-changed).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from clickcast.cli import app
from clickcast.feedback import write
from clickcast.feedback.assertions import (
    ASSERTIONS_SCHEMA_VERSION,
    build_assertions,
    diff_assertions,
    load_assertions,
)
from clickcast.feedback.models import (
    Assertions,
    Media,
    PageState,
    Report,
    StepAssertion,
    StepReport,
)

REPO_ROOT = Path(__file__).parent.parent
ASSERTIONS_SCHEMA_PATH = REPO_ROOT / "docs" / "assertions-schema" / "v1.json"


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _media() -> Media:
    return Media(
        path="tour.gif",
        format="gif",
        size_bytes=1024,
        frame_count=12,
        duration_s=1.0,
        fps=12,
    )


def _sample_report(
    *,
    started_at: str = "2026-07-23T15:00:00+00:00",
    goto_url: str = "https://acme.example/app",
    frame_name: str = "frame-0000-000.png",
    console_errors: list[str] | None = None,
) -> Report:
    """Realistic-shaped sidecar with two steps, one console error.

    The stability tests below vary ``started_at``, ``goto_url`` (query string),
    and ``frame_name`` to prove :func:`build_assertions` scrubs them.
    """
    return Report(
        clickcast_version="0.1.3",
        url=goto_url,
        started_at=started_at,
        duration_s=3.25,
        media=_media(),
        steps=[
            StepReport(
                index=0,
                action="goto",
                args={"url": goto_url},
                status="ok",
                duration_ms=1200.0,
                frames=[frame_name],
                label="Open site",
                page_state=PageState(
                    title="Acme",
                    url_after=goto_url,
                    console_errors=list(console_errors or []),
                ),
            ),
            StepReport(
                index=1,
                action="click",
                args={"selector": "#cta"},
                status="ok",
                duration_ms=42.0,
                frames=["frame-0001-000.png"],
                label="Click CTA",
                page_state=PageState(title="Acme"),
            ),
        ],
    )


# ------------------------------------------------------------------
# Model
# ------------------------------------------------------------------


class TestModel:
    def test_extra_forbid_on_top_level(self) -> None:
        with pytest.raises(ValidationError):
            Assertions.model_validate(
                {
                    "schema_version": 1,
                    "step_count": 0,
                    "steps": [],
                    "surprise": True,
                }
            )

    def test_extra_forbid_on_step(self) -> None:
        with pytest.raises(ValidationError):
            StepAssertion.model_validate(
                {
                    "action": "click",
                    "label": None,
                    "status": "ok",
                    "console_error_count": 0,
                    "page_error_count": 0,
                    "network_failed_count": 0,
                    "duration_ms": 42.0,  # not in the assertion contract
                }
            )

    def test_counts_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            StepAssertion(
                action="click",
                status="ok",
                console_error_count=-1,
                page_error_count=0,
                network_failed_count=0,
            )


# ------------------------------------------------------------------
# build_assertions — shape stability
# ------------------------------------------------------------------


class TestBuildShape:
    def test_top_level_keys_and_schema_version(self) -> None:
        out = build_assertions(_sample_report())
        assert set(out) == {"schema_version", "step_count", "steps"}
        assert out["schema_version"] == ASSERTIONS_SCHEMA_VERSION
        assert out["step_count"] == 2

    def test_step_keys_are_the_contract(self) -> None:
        out = build_assertions(_sample_report())
        assert set(out["steps"][0]) == {
            "action",
            "label",
            "status",
            "console_error_count",
            "page_error_count",
            "network_failed_count",
            # See #151 (AI-2, AI-5): schema-v3 gate fields joined the
            # assertion contract so CI baselines can pin skip / error KIND.
            "skip_reason",
            "error_code",
        }

    def test_excludes_timing_frames_cursor_from_step_rows(self) -> None:
        out = build_assertions(_sample_report())
        for row in out["steps"]:
            assert "duration_ms" not in row
            assert "frames" not in row
            assert "cursor_xy" not in row
            assert "args" not in row
            # And no resolved URL leakage either.
            assert "url" not in row
            assert "url_after" not in row

    def test_console_error_count_reflects_page_state(self) -> None:
        out = build_assertions(_sample_report(console_errors=["boom", "kapow", "ouch"]))
        assert out["steps"][0]["console_error_count"] == 3
        assert out["steps"][1]["console_error_count"] == 0

    def test_missing_page_state_counts_as_zero(self) -> None:
        report = Report(
            clickcast_version="0.1.3",
            started_at="2026-07-23T15:00:00+00:00",
            duration_s=0.5,
            media=_media(),
            steps=[
                StepReport(
                    index=0,
                    action="wait",
                    args={"wait": 1.0},
                    status="ok",
                    duration_ms=1000.0,
                    frames=[],
                    label=None,
                )
            ],
        )
        out = build_assertions(report)
        assert out["steps"][0]["console_error_count"] == 0
        assert out["steps"][0]["page_error_count"] == 0
        assert out["steps"][0]["network_failed_count"] == 0

    def test_round_trips_through_pydantic_contract(self) -> None:
        # The distillation must satisfy the Assertions model — otherwise the
        # shape has drifted from the schema without anyone noticing.
        out = build_assertions(_sample_report())
        Assertions.model_validate(out)  # no ValidationError

    def test_skip_reason_and_error_code_flow_through(self) -> None:
        # See #151 (AI-2, AI-5): the two schema-v3 gates must reach the
        # assertion row so CI baselines can pin the KIND of skip / failure.
        report = _sample_report()
        report.steps[1] = StepReport(
            index=1,
            action="click",
            args={"selector": "#gone"},
            status="skipped",
            duration_ms=8.0,
            frames=[],
            label="optional CTA",
            page_state=PageState(),
            skip_reason="element_vanished",
            error_code="locator_missing",
        )
        out = build_assertions(report)
        assert out["steps"][1]["skip_reason"] == "element_vanished"
        assert out["steps"][1]["error_code"] == "locator_missing"
        # Successful step 0 carries None for both.
        assert out["steps"][0]["skip_reason"] is None
        assert out["steps"][0]["error_code"] is None

    def test_different_skip_reasons_produce_different_bytes(self) -> None:
        # A step going from ``skipped(pre_action_failed)`` to
        # ``skipped(element_vanished)`` is real behavioural drift — the
        # distillation must reflect it. Guards against the shipped-v2
        # regression where two very different skips were byte-identical.
        report_a = _sample_report()
        report_a.steps[1] = StepReport(
            index=1,
            action="click",
            args={"selector": "#x"},
            status="skipped",
            duration_ms=1.0,
            frames=[],
            label="CTA",
            page_state=PageState(),
            skip_reason="pre_action_failed",
        )
        report_b = _sample_report()
        report_b.steps[1] = StepReport(
            index=1,
            action="click",
            args={"selector": "#x"},
            status="skipped",
            duration_ms=1.0,
            frames=[],
            label="CTA",
            page_state=PageState(),
            skip_reason="element_vanished",
        )
        a = json.dumps(build_assertions(report_a), sort_keys=True)
        b = json.dumps(build_assertions(report_b), sort_keys=True)
        assert a != b


class TestByteIdenticalAcrossNoise:
    """The whole point — same UI, different run → identical JSON bytes."""

    @staticmethod
    def _bytes(report: Report) -> str:
        return json.dumps(build_assertions(report), sort_keys=True, indent=2)

    def test_timestamp_change_does_not_alter_bytes(self) -> None:
        a = self._bytes(_sample_report(started_at="2026-07-23T15:00:00+00:00"))
        b = self._bytes(_sample_report(started_at="2026-08-01T09:30:15+00:00"))
        assert a == b

    def test_frame_path_change_does_not_alter_bytes(self) -> None:
        a = self._bytes(_sample_report(frame_name="frame-0000-000.png"))
        b = self._bytes(_sample_report(frame_name="frame-9999-042.png"))
        assert a == b

    def test_url_query_string_change_does_not_alter_bytes(self) -> None:
        # This is the Vercel-bypass-token scenario: same target URL, different
        # short-lived query string every run. Baseline diffs must not flap.
        a = self._bytes(
            _sample_report(
                goto_url="https://acme.example/app?x-vercel-protection-bypass=abc123&extra=1",
            )
        )
        b = self._bytes(
            _sample_report(
                goto_url="https://acme.example/app?x-vercel-protection-bypass=xyz789&extra=2",
            )
        )
        assert a == b

    def test_url_path_change_does_alter_nothing_in_assertions(self) -> None:
        # Even path changes in URL don't affect the distillation — the
        # distillation is about behavior, not the URL. This is expected:
        # if the assertion set should be URL-sensitive, the caller should
        # keep the raw sidecar around too.
        a = self._bytes(_sample_report(goto_url="https://acme.example/app"))
        b = self._bytes(_sample_report(goto_url="https://acme.example/dashboard"))
        assert a == b


# ------------------------------------------------------------------
# diff_assertions — regression-gate signal
# ------------------------------------------------------------------


class TestDiff:
    def test_equal_reports_clean(self) -> None:
        base = build_assertions(_sample_report())
        drift, clean = diff_assertions(base, base)
        assert clean is True
        assert drift == []

    def test_step_added_reports_added_index(self) -> None:
        base_report = _sample_report()
        added_report = _sample_report()
        added_report.steps.append(
            StepReport(
                index=2,
                action="scroll",
                args={"by": 600},
                status="ok",
                duration_ms=10.0,
                frames=[],
                label="Scroll down",
                page_state=PageState(),
            )
        )
        drift, clean = diff_assertions(
            build_assertions(added_report), build_assertions(base_report)
        )
        assert clean is False
        assert any("step_count changed 2 -> 3" in d for d in drift)
        assert any("step 2: added" in d and "scroll" in d for d in drift)

    def test_step_status_changed_reports_ok_to_failed(self) -> None:
        base_report = _sample_report()
        current_report = _sample_report()
        current_report.steps[1] = StepReport(
            index=1,
            action="click",
            args={"selector": "#cta"},
            status="failed",
            duration_ms=42.0,
            frames=[],
            label="Click CTA",
            page_state=PageState(),
            error="TimeoutError",
        )
        drift, clean = diff_assertions(
            build_assertions(current_report), build_assertions(base_report)
        )
        assert clean is False
        assert any("step 1: status changed 'ok' -> 'failed'" in d for d in drift)

    def test_console_error_count_change_reported(self) -> None:
        base_report = _sample_report()
        current_report = _sample_report(console_errors=["boom", "kapow"])
        drift, clean = diff_assertions(
            build_assertions(current_report), build_assertions(base_report)
        )
        assert clean is False
        assert any("step 0: console_error_count changed 0 -> 2" in d for d in drift)

    def test_step_removed_reports_removed_index(self) -> None:
        base_report = _sample_report()
        current_report = _sample_report()
        current_report.steps.pop()
        drift, clean = diff_assertions(
            build_assertions(current_report), build_assertions(base_report)
        )
        assert clean is False
        assert any("step 1: removed" in d for d in drift)

    def test_drift_order_is_deterministic(self) -> None:
        # Same inputs → identical drift list. CI diffs shouldn't reshuffle.
        base_report = _sample_report()
        current_report = _sample_report(console_errors=["e1"])
        current_report.steps.append(
            StepReport(
                index=2,
                action="scroll",
                args={"by": 100},
                status="ok",
                duration_ms=5.0,
                frames=[],
                label=None,
                page_state=PageState(),
            )
        )
        cur = build_assertions(current_report)
        base = build_assertions(base_report)
        d1, _ = diff_assertions(cur, base)
        d2, _ = diff_assertions(cur, base)
        assert d1 == d2


# ------------------------------------------------------------------
# load_assertions
# ------------------------------------------------------------------


class TestLoadAssertions:
    def test_round_trip_via_json(self, tmp_path: Path) -> None:
        payload = build_assertions(_sample_report())
        path = tmp_path / "golden.json"
        path.write_text(json.dumps(payload, indent=2))
        loaded = load_assertions(path)
        assert loaded == payload

    def test_rejects_non_object(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("[1, 2, 3]")
        with pytest.raises(ValueError, match="did not deserialize"):
            load_assertions(path)


# ------------------------------------------------------------------
# Schema file
# ------------------------------------------------------------------


class TestSchemaFile:
    def test_schema_file_exists_and_declares_v1(self) -> None:
        assert ASSERTIONS_SCHEMA_PATH.exists()
        schema = json.loads(ASSERTIONS_SCHEMA_PATH.read_text())
        assert schema["properties"]["schema_version"]["const"] == 1

    def test_schema_matches_build_assertions_shape(self) -> None:
        # Every required top-level key in the schema must appear in the payload.
        schema = json.loads(ASSERTIONS_SCHEMA_PATH.read_text())
        out = build_assertions(_sample_report())
        for key in schema["required"]:
            assert key in out
        step_required: set[str] = set(schema["$defs"]["StepAssertion"]["required"])
        for row in out["steps"]:
            assert step_required.issubset(row.keys()), (
                f"step row missing schema-required keys: {step_required - set(row)}"
            )


# ------------------------------------------------------------------
# CLI — `clickcast assertions`
# ------------------------------------------------------------------


class TestCLI:
    runner = CliRunner()

    def _write_sidecar(self, tmp_path: Path, report: Report) -> Path:
        return write(report, tmp_path / "reel.gif.json")

    def _write_baseline(self, tmp_path: Path, payload: dict[str, Any]) -> Path:
        p = tmp_path / "golden.json"
        p.write_text(json.dumps(payload, indent=2))
        return p

    def test_default_emits_distilled_json(self, tmp_path: Path) -> None:
        sidecar = self._write_sidecar(tmp_path, _sample_report())
        result = self.runner.invoke(app, ["assertions", str(sidecar)])
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert payload["schema_version"] == ASSERTIONS_SCHEMA_VERSION
        assert payload["step_count"] == 2

    def test_matches_baseline_exits_zero(self, tmp_path: Path) -> None:
        report = _sample_report()
        sidecar = self._write_sidecar(tmp_path, report)
        baseline = self._write_baseline(tmp_path, build_assertions(report))
        result = self.runner.invoke(app, ["assertions", str(sidecar), "--baseline", str(baseline)])
        assert result.exit_code == 0
        assert "no drift" in result.stdout

    def test_drift_exits_nonzero_and_prints_drift(self, tmp_path: Path) -> None:
        baseline_report = _sample_report()
        current_report = _sample_report(console_errors=["boom"])
        sidecar = self._write_sidecar(tmp_path, current_report)
        baseline = self._write_baseline(tmp_path, build_assertions(baseline_report))
        result = self.runner.invoke(app, ["assertions", str(sidecar), "--baseline", str(baseline)])
        assert result.exit_code == 1
        assert "console_error_count changed" in result.stdout

    def test_json_drift_output_shape(self, tmp_path: Path) -> None:
        baseline_report = _sample_report()
        current_report = _sample_report(console_errors=["boom"])
        sidecar = self._write_sidecar(tmp_path, current_report)
        baseline = self._write_baseline(tmp_path, build_assertions(baseline_report))
        result = self.runner.invoke(
            app,
            ["assertions", str(sidecar), "--baseline", str(baseline), "--json"],
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["is_clean"] is False
        assert isinstance(payload["drift"], list) and payload["drift"]
        assert payload["current"]["schema_version"] == ASSERTIONS_SCHEMA_VERSION

    def test_missing_sidecar_errors(self, tmp_path: Path) -> None:
        result = self.runner.invoke(app, ["assertions", str(tmp_path / "nope.json")])
        assert result.exit_code != 0
        assert "sidecar not found" in result.output

    def test_missing_baseline_errors(self, tmp_path: Path) -> None:
        sidecar = self._write_sidecar(tmp_path, _sample_report())
        result = self.runner.invoke(
            app,
            ["assertions", str(sidecar), "--baseline", str(tmp_path / "no.json")],
        )
        assert result.exit_code != 0
        assert "baseline not found" in result.output
