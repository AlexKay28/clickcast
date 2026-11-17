"""The shared sidecar assembly path (#257).

Four implementations had drifted: `auto`/`run`/`Reel`/MCP each built the media
block, finalized the report and wrote the file their own way. These tests pin
the single implementation (`clickcast.kernel.sidecar`) and the drift fixes that
came with it: a derived report-level `url`, per-step frame paths, redaction on
live transcripts, and `with_feedback` from the SDK.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from clickcast.core.actions import ActionResult, GotoStep
from clickcast.encode import EncodeResult
from clickcast.feedback import ReportBuilder
from clickcast.kernel import (
    make_media,
    sidecar_path,
    transcript_media,
    write_media_sidecar,
    write_sidecar,
)

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[1] / "src" / "clickcast"


def _enc(tmp_path: Path) -> EncodeResult:
    return EncodeResult(
        path=tmp_path / "reel.gif",
        format="gif",
        size_bytes=123,
        duration_s=2.0,
        frame_count=24,
    )


def _ok_goto(url: str) -> ActionResult:
    return ActionResult(ok=True, status="ok", action="goto")


class TestMakeMedia:
    def test_carries_every_encoder_field_and_the_plan_fps(self, tmp_path: Path) -> None:
        media = make_media(_enc(tmp_path), fps=24)
        assert media.path == str(tmp_path / "reel.gif")
        assert media.format == "gif"
        assert media.size_bytes == 123
        assert media.frame_count == 24
        assert media.duration_s == 2.0
        # fps comes from the render plan, not from EncodeResult (which has none).
        assert media.fps == 24

    def test_transcript_media_is_schema_valid_without_a_reel(self) -> None:
        media = transcript_media(step_count=3)
        assert media.model_dump() == {
            "path": "",
            "format": "none",
            "size_bytes": 0,
            "frame_count": 3,
            "duration_s": 0.0,
            "fps": 1,
        }


class TestSidecarPath:
    def test_appends_rather_than_replaces_the_suffix(self) -> None:
        assert sidecar_path("tour.gif") == Path("tour.gif.json")
        assert sidecar_path(Path("a/tour.mp4")) == Path("a/tour.mp4.json")

    def test_suffixless_path_still_gets_one(self) -> None:
        assert sidecar_path("tour") == Path("tour.json")


class TestWriteSidecar:
    async def _report(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        builder = ReportBuilder(url="https://example.test/", engine="chromium")
        await builder.record_step(
            index=0,
            step=GotoStep(url="https://example.test/"),
            result=_ok_goto("https://example.test/"),
        )
        return builder.build(make_media(_enc(tmp_path), fps=12))

    async def test_writes_next_to_the_media(self, tmp_path: Path) -> None:
        report = await self._report(tmp_path)
        written = write_sidecar(tmp_path / "reel.gif", report)
        assert written == tmp_path / "reel.gif.json"
        assert json.loads(written.read_text())["schema_version"] == 4

    async def test_no_sidecar_and_missing_report_are_no_ops(self, tmp_path: Path) -> None:
        report = await self._report(tmp_path)
        assert write_sidecar(tmp_path / "reel.gif", report, no_sidecar=True) is None
        assert write_sidecar(tmp_path / "reel.gif", None) is None
        assert not (tmp_path / "reel.gif.json").exists()

    async def test_target_overrides_the_derived_path(self, tmp_path: Path) -> None:
        """A live MCP transcript names its own file — there is no media path."""
        report = await self._report(tmp_path)
        target = tmp_path / "transcript.json"
        assert write_sidecar("ignored", report, target=target) == target
        assert target.exists()

    async def test_redaction_and_feedback_reach_the_writer(self, tmp_path: Path) -> None:
        report = await self._report(tmp_path)
        written = write_sidecar(
            tmp_path / "reel.gif",
            report,
            redact_patterns=[re.compile("example")],
            with_feedback=True,
        )
        assert written is not None
        data = json.loads(written.read_text())
        assert "example" not in data["url"]
        assert data["feedback"]["report_url"]

    async def test_write_media_sidecar_composes_the_three_steps(self, tmp_path: Path) -> None:
        builder = ReportBuilder(url="https://example.test/", engine="chromium")
        await builder.record_step(
            index=0, step=GotoStep(url="https://example.test/"), result=_ok_goto("x")
        )
        written = write_media_sidecar(tmp_path / "reel.gif", builder, _enc(tmp_path), 24)
        assert written is not None
        data = json.loads(written.read_text())
        assert data["media"]["fps"] == 24
        assert data["url"] == "https://example.test/"


class TestReportUrlDerivation:
    """`run` and live MCP sessions never passed a url to the builder; the first
    `goto` now supplies it, so their sidecars stop reporting `url: null`."""

    async def test_first_goto_sets_the_report_url(self) -> None:
        builder = ReportBuilder(engine="chromium")
        await builder.record_step(
            index=0, step=GotoStep(url="https://a.test/start"), result=_ok_goto("x")
        )
        report = builder.build(transcript_media(1))
        assert report.url == "https://a.test/start"

    async def test_explicit_url_wins_over_the_goto(self) -> None:
        builder = ReportBuilder(url="https://explicit.test/", engine="chromium")
        await builder.record_step(
            index=0, step=GotoStep(url="https://a.test/start"), result=_ok_goto("x")
        )
        assert builder.build(transcript_media(1)).url == "https://explicit.test/"

    async def test_later_gotos_do_not_rewrite_the_url(self) -> None:
        builder = ReportBuilder(engine="chromium")
        await builder.record_step(
            index=0, step=GotoStep(url="https://a.test/one"), result=_ok_goto("x")
        )
        await builder.record_step(
            index=1, step=GotoStep(url="https://a.test/two"), result=_ok_goto("x")
        )
        assert builder.build(transcript_media(2)).url == "https://a.test/one"

    async def test_steps_record_their_frames(self) -> None:
        builder = ReportBuilder(engine="chromium")
        await builder.record_step(
            index=0,
            step=GotoStep(url="https://a.test/"),
            result=_ok_goto("x"),
            frames=[Path("/tmp/step-0000.png")],
        )
        assert builder.steps[0].frames == ["step-0000.png"]


class TestSingleImplementation:
    def test_no_surface_reimplements_media_or_sidecar_assembly(self) -> None:
        """A local `_write_sidecar`/`_make_media` twin is exactly how the four
        copies drifted. Only the kernel may define them."""
        offenders: list[str] = []
        for path in SRC.rglob("*.py"):
            if path.parent.name == "kernel":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in {
                    "_write_sidecar",
                    "_make_media",
                    "_build_report_if_needed",
                    "_write_sidecar_from_report",
                }:
                    offenders.append(f"{path.relative_to(SRC)}:{node.name}")
        assert offenders == []

    def test_media_is_constructed_only_in_the_kernel_and_schema_module(self) -> None:
        """`Media(...)` has six required fields; four hand-rolled constructions
        is how a fabricated `format="none"` shape appeared. Kernel or models."""
        allowed = {Path("kernel/sidecar.py"), Path("feedback/models.py")}
        offenders: list[str] = []
        for path in SRC.rglob("*.py"):
            relative = path.relative_to(SRC)
            if relative in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Media"
                ):
                    offenders.append(str(relative))
        assert offenders == []


class TestReelSaveUsesTheKernel:
    """`Reel.save` used to finalize and write its own way, with no way to ask
    for the feedback block at all (#257)."""

    def test_save_accepts_with_feedback_and_writes_it(self, tmp_path: Path) -> None:
        # Signature parity: the sync facade and the async implementation agree.
        import inspect

        from clickcast.reel import AsyncReel, Reel

        assert "with_feedback" in inspect.signature(Reel.save).parameters
        assert "with_feedback" in inspect.signature(AsyncReel.save).parameters

        reel = Reel("https://example.test/", fps=6).goto()
        enc = _enc(tmp_path)
        with patch(
            "clickcast.reel._run_and_encode",
            return_value=(SimpleNamespace(ok=True, failed_at=None), enc, 6),
        ):
            reel.save(tmp_path / "reel.gif", with_feedback=True)

        data = json.loads((tmp_path / "reel.gif.json").read_text())
        assert data["feedback"]["report_url"]
        assert data["media"]["fps"] == 6
        assert data["url"] == "https://example.test/"

    def test_save_still_keeps_the_report_for_assertions_with_no_sidecar(
        self, tmp_path: Path
    ) -> None:
        from clickcast.reel import Reel

        reel = Reel("https://example.test/", fps=6).goto()
        enc = _enc(tmp_path)
        with patch(
            "clickcast.reel._run_and_encode",
            return_value=(SimpleNamespace(ok=True, failed_at=None), enc, 6),
        ):
            reel.save(tmp_path / "reel.gif", no_sidecar=True)

        assert reel._last_report is not None
        assert not (tmp_path / "reel.gif.json").exists()
