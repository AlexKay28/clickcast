"""Tests for ``receipt()`` (#271) — the closing call of the perception loop.

Two layers:

* pure-summary tests hand-build :class:`~clickcast.feedback.Report` objects
  (no browser) and assert the ``receipt/1`` contract, the token fixed point,
  ``top_error`` semantics and the artifact set;
* integration tests run a real 10-step tour against ``tests/fixtures/site/``
  and drive a live MCP session, including a playable MP4 encode through the
  shared render kernel.

The token number is never hard-coded: every assertion recomputes
``estimate_tokens(compact_json(payload))`` from the exact returned payload,
so the ≤200-token contract cannot drift away from what consumers receive.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import imageio.v2 as iio
import pytest

from clickcast import Reel
from clickcast.feedback import Media, Report, StepReport
from clickcast.feedback.assertions import build_assertions, diff_assertions
from clickcast.perception.receipt import (
    ASSERTIONS_FILENAME,
    RECEIPT_FILENAME,
    RECEIPT_TOKEN_BUDGET,
    SCHEMA,
    build_receipt,
    write_receipt,
)
from clickcast.perception.tokens import compact_json, estimate_tokens
from tests.mcp.conftest import live_session, result_json

REPO_ROOT = Path(__file__).parent.parent

_MODULE = REPO_ROOT / "src" / "clickcast" / "perception" / "receipt.py"


def _report(
    *,
    statuses: list[str] | None = None,
    error_codes: list[str | None] | None = None,
    media_path: str = "tour.mp4",
    frame_count: int = 170,
    duration_s: float = 14.2,
) -> Report:
    statuses = statuses or ["ok"] * 10
    codes = error_codes or [None] * len(statuses)
    steps = [
        StepReport(
            index=index,
            action="click",
            status=status,
            duration_ms=1.0,
            error_code=code,  # type: ignore[arg-type]
        )
        for index, (status, code) in enumerate(zip(statuses, codes, strict=True))
    ]
    return Report(
        clickcast_version="0.0.0-test",
        started_at="2026-01-01T00:00:00Z",
        duration_s=duration_s,
        media=Media(
            path=media_path,
            format="mp4",
            size_bytes=1,
            frame_count=frame_count,
            duration_s=duration_s,
            fps=12,
        ),
        steps=steps,
    )


def _tokens(payload: dict) -> int:
    return estimate_tokens(compact_json(payload))


# ---------------------------------------------------------------------------
# Summary shape
# ---------------------------------------------------------------------------


class TestSummaryUnit:
    def test_shape_matches_the_contract(self) -> None:
        report = _report(
            statuses=["ok"] * 4 + ["failed"] + ["ok"] * 5,
            error_codes=[None] * 4 + ["timeout"] + [None] * 5,
        )
        summary = build_receipt(
            report,
            out_dir="/artifacts",
            media_path="/artifacts/tour.mp4",
            sidecar_path="/artifacts/tour.mp4.json",
            drift=2,
            baseline="/artifacts/baseline.json",
        )
        assert summary["schema"] == SCHEMA == "receipt/1"
        assert summary["media"] == {"path": "tour.mp4", "duration_s": 14.2, "frames": 170}
        assert summary["sidecar"] == "tour.mp4.json"
        assert summary["sidecar_version"] == 5
        assert summary["steps"] == {"ok": 9, "failed": 1, "skipped": 0}
        assert summary["top_error"] == {"step": 4, "error_code": "timeout"}
        assert summary["assertions"] == {"drift": 2, "baseline": "baseline.json"}
        assert "visual_diff" not in summary

    def test_summary_of_a_ten_step_tour_is_within_budget(self) -> None:
        summary = build_receipt(
            _report(),
            out_dir="/artifacts",
            media_path="/artifacts/tour.mp4",
            sidecar_path="/artifacts/tour.mp4.json",
            drift=0,
            baseline="/artifacts/baseline.json",
        )
        assert summary["tokens"]["budget"] == RECEIPT_TOKEN_BUDGET == 200
        assert summary["tokens"]["estimated"] == _tokens(summary)
        assert summary["tokens"]["estimated"] <= 200
        # Details stay out of the summary: only counts, never step records.
        assert set(summary["steps"]) == {"ok", "failed", "skipped"}
        assert "frames" not in summary
        assert "steps_detail" not in summary

    def test_top_error_is_the_first_failed_step(self) -> None:
        report = _report(
            statuses=["ok", "failed", "failed", "ok"],
            error_codes=[None, "locator_missing", "timeout", None],
        )
        summary = build_receipt(report, out_dir="/a")
        assert summary["top_error"] == {"step": 1, "error_code": "locator_missing"}

    def test_top_error_is_omitted_when_every_step_passes(self) -> None:
        summary = build_receipt(_report(), out_dir="/a")
        assert "top_error" not in summary

    def test_optional_blocks_are_omitted_when_not_requested(self) -> None:
        summary = build_receipt(_report(), out_dir="/a")
        assert "assertions" not in summary
        assert "visual_diff" not in summary
        assert "media" not in summary
        assert "sidecar" not in summary

    def test_budget_is_enforced_by_dropping_optional_blocks(self) -> None:
        visual = {
            "steps": [
                {"run_index": 3, "changed_pct": 6.4},
                {"run_index": 7, "changed_pct": 0.0},
            ]
        }
        generous = build_receipt(
            _report(),
            out_dir="/a",
            drift=1,
            baseline="/a/baseline.json",
            visual_diff=visual,
        )
        assert generous["visual_diff"] == {"changed_steps": [3], "max_changed_pct": 6.4}
        assert _tokens(generous) <= 200

        tight = build_receipt(
            _report(),
            out_dir="/a",
            drift=1,
            baseline="/a/baseline.json",
            visual_diff=visual,
            token_budget=50,
        )
        assert "visual_diff" not in tight
        assert _tokens(tight) <= 50

    def test_visual_diff_rounds_and_lists_only_changed_steps(self) -> None:
        summary = build_receipt(
            _report(),
            out_dir="/a",
            visual_diff={"steps": [{"run_index": 3, "changed_pct": 6.444}]},
        )
        assert summary["visual_diff"] == {"changed_steps": [3], "max_changed_pct": 6.4}


# ---------------------------------------------------------------------------
# Artifact directory
# ---------------------------------------------------------------------------


class TestArtifactDirectory:
    def test_every_artifact_lands_in_one_directory(self, tmp_path: Path) -> None:
        report = _report()
        out = tmp_path / "artifacts"
        out.mkdir()
        media = out / "tour.mp4"
        media.write_bytes(b"not-really-mp4")
        # Sidecar through the same writer production uses.
        from clickcast.kernel import write_sidecar

        sidecar = write_sidecar(media, report)
        assert sidecar is not None

        baseline = tmp_path / "baseline.json"
        baseline.write_text(json.dumps(build_assertions(_report(statuses=["failed"] * 10))))

        summary = write_receipt(
            report,
            out,
            media_path=media,
            sidecar_path=sidecar,
            baseline=baseline,
        )

        assert summary["sidecar"] == "tour.mp4.json"
        assert summary["media"]["path"] == "tour.mp4"
        assert (out / RECEIPT_FILENAME).is_file()
        assert (out / ASSERTIONS_FILENAME).is_file()
        assert (out / baseline.name).is_file()
        assert json.loads((out / RECEIPT_FILENAME).read_text()) == summary

        # The written sidecar validates under the v5 model.
        loaded = Report.model_validate(json.loads((out / "tour.mp4.json").read_text()))
        assert loaded.schema_version == 5
        assert len(loaded.steps) == 10

        # The summary's drift count is exactly the source-data drift.
        drift, clean = diff_assertions(
            build_assertions(loaded), build_assertions(_report(statuses=["failed"] * 10))
        )
        assert not clean
        assert summary["assertions"] == {"drift": len(drift), "baseline": baseline.name}

    def test_no_media_sidecar_is_named_transcript(self, tmp_path: Path) -> None:
        from clickcast.kernel import transcript_media, write_sidecar

        report = _report()
        out = tmp_path / "artifacts"
        out.mkdir()
        target = out / "transcript.json"
        sidecar = write_sidecar(
            target,
            report.model_copy(update={"media": transcript_media(len(report.steps))}),
            target=target,
        )
        summary = write_receipt(report, out, sidecar_path=sidecar)
        assert summary["sidecar"] == "transcript.json"
        assert "media" not in summary

    def test_visual_diff_artifact_is_written(self, tmp_path: Path) -> None:
        out = tmp_path / "artifacts"
        out.mkdir()
        summary = write_receipt(
            _report(),
            out,
            visual_diff={"steps": [{"run_index": 2, "changed_pct": 1.5}]},
        )
        assert (out / "visual_diff.json").is_file()
        assert summary["visual_diff"] == {"changed_steps": [2], "max_changed_pct": 1.5}


# ---------------------------------------------------------------------------
# Reel.receipt — real 10-step tour
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReelReceipt:
    def test_ten_step_fixture_tour(self, fixture_site_url: str, tmp_path: Path) -> None:
        out = tmp_path / "tour"
        reel = (
            Reel(fixture_site_url + "/", viewport=(1280, 800), fps=6, dwell=0.02)
            .goto(wait="load")
            .click("#btn-3d")
            .click("#btn-compare")
            .hover("#btn-compare")
            .scroll(by=120)
            .wait(0.05)
            .screenshot()
            .goto(fixture_site_url + "/tabs.html", wait="load")
            .click("#tab-details")
            .click("#tab-overview")
        )
        assert len(reel.steps) == 10

        summary = reel.receipt(out)

        assert summary["steps"] == {"ok": 10, "failed": 0, "skipped": 0}
        assert "top_error" not in summary
        assert summary["tokens"]["estimated"] == _tokens(summary)
        assert _tokens(summary) <= 200

        assert (out / RECEIPT_FILENAME).is_file()
        sidecar_path = out / summary["sidecar"]
        assert sidecar_path.is_file()
        loaded = Report.model_validate(json.loads(sidecar_path.read_text()))
        assert loaded.schema_version == 5
        assert len(loaded.steps) == 10
        # The detail exists only in the sidecar.
        assert all(step.frames for step in loaded.steps)
        assert "frames" not in compact_json(summary)

    def test_receipt_reuses_an_already_run_reel(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        reel = Reel(fixture_site_url + "/", viewport=(1280, 800), fps=6, dwell=0.02).goto(
            wait="load"
        )
        media = reel.save(tmp_path / "tour.gif")
        summary = reel.receipt(tmp_path / "receipt")
        # No re-run: the saved media is carried into the artifact directory.
        assert (tmp_path / "receipt" / Path(media).name).is_file()
        assert summary["media"]["path"] == Path(media).name
        assert summary["steps"]["ok"] == 1

    def test_receipt_with_media_encodes_a_playable_mp4(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        reel = (
            Reel(fixture_site_url + "/", viewport=(640, 480), fps=4, dwell=0.02)
            .goto(wait="load")
            .click("#btn-3d")
        )
        summary = reel.receipt(tmp_path / "tour", media=True)
        media = tmp_path / "tour" / summary["media"]["path"]
        assert media.suffix == ".mp4"
        assert media.stat().st_size > 0
        with iio.get_reader(media) as reader:
            frames = [frame for frame in reader]
        assert frames
        assert summary["media"]["frames"] > 0
        assert summary["media"]["duration_s"] > 0
        assert summary["sidecar_version"] == 5


# ---------------------------------------------------------------------------
# MCP receipt
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpReceipt:
    async def test_receipt_from_a_live_session_writes_a_playable_mp4(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        out = tmp_path / "live"
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            await client.call_tool("click", {"selector": "#btn-3d"})
            await client.call_tool("click", {"selector": "#btn-compare"})
            result = await client.call_tool("receipt", {"media": True, "out": str(out)})
            # Closing semantics stay explicit: the session is still live.
            probe = await client.call_tool("observe", {})

        assert result.isError is not True
        assert probe.isError is not True
        summary = result_json(result)
        assert summary["schema"] == "receipt/1"
        assert summary["steps"] == {"ok": 3, "failed": 0, "skipped": 0}
        assert summary["tokens"]["estimated"] == _tokens(summary)
        assert _tokens(summary) <= 200

        media = out / summary["media"]["path"]
        assert media.stat().st_size > 0
        with iio.get_reader(media) as reader:
            assert [frame for frame in reader]

        sidecar = Report.model_validate(json.loads((out / summary["sidecar"]).read_text()))
        assert sidecar.schema_version == 5
        assert len(sidecar.steps) == 3
        assert (out / ASSERTIONS_FILENAME).is_file()

    async def test_top_error_matches_the_first_failed_step(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        out = tmp_path / "live"
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            failed = await client.call_tool(
                "click", {"selector": "#never-there", "timeout_ms": 250}
            )
            assert failed.isError is True
            await client.call_tool("click", {"selector": "#btn-3d"})
            result = await client.call_tool("receipt", {"out": str(out)})

        summary = result_json(result)
        sidecar = Report.model_validate(json.loads((out / summary["sidecar"]).read_text()))
        first_failed = next(i for i, step in enumerate(sidecar.steps) if step.status == "failed")
        assert summary["top_error"] == {
            "step": first_failed,
            "error_code": sidecar.steps[first_failed].error_code,
        }
        assert summary["steps"]["failed"] == 1
        assert summary["steps"]["ok"] == 2

    async def test_receipt_without_media_omits_the_media_block(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        out = tmp_path / "live"
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("receipt", {"out": str(out)})
        summary = result_json(result)
        assert "media" not in summary
        assert summary["sidecar"] == "transcript.json"
        assert Report.model_validate(json.loads((out / "transcript.json").read_text()))

    async def test_receipt_requires_a_session(self) -> None:
        from tests.mcp.conftest import open_client

        async with open_client() as client:
            result = await client.call_tool("receipt", {})
        assert result.isError is True
        assert "no active session" in result_json(result)["error"]


# ---------------------------------------------------------------------------
# Architecture: receipt.py never reaches the encoder
# ---------------------------------------------------------------------------


class TestArchitecture:
    def test_receipt_module_does_not_import_the_encoder(self) -> None:
        tree = ast.parse(_MODULE.read_text(encoding="utf-8"), filename=str(_MODULE))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert "clickcast.encode" not in imported
        assert not any(name.startswith("clickcast.encode") for name in imported)
        assert not any(name.startswith("playwright") for name in imported)
        assert not any(name.startswith("typer") for name in imported)
        assert "clickcast.cli" not in imported
