"""Screencast capture backend (#303).

Unit tests cover the backend contract (validation, Chromium-only failure,
the shots manifest staying byte-identical). The integration test drives a
real Chromium screencast over the 300 ms transition fixture, encodes the
result through the normal kernel and asserts *properties* of the motion —
screencast frame counts depend on wall time, so golden pixels cannot exist.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image
from scripts.bench_motion import measure

from clickcast.annotate import AnnotateConfig
from clickcast.capture import Recorder, ScreencastSource, ScreencastUnavailable
from clickcast.core.actions import ClickStep, GotoStep, execute
from clickcast.core.session import Session
from clickcast.core.viewport import Viewport
from clickcast.encode import encode
from clickcast.feedback import ReportBuilder
from clickcast.kernel import RenderPlan, render_reel
from clickcast.scenario import Meta, Scenario
from clickcast.scenario import run as run_scenario

SCREENSHOT_KEYS = {"path", "step_index", "sub_index", "cursor_xy"}
MANIFEST_KEYS = {"fps", "count", "frames"}


class _ShotSession:
    """Minimal session for the shots backend: writes a real PNG per capture."""

    engine = "chromium"

    async def screenshot(self, path: str | Path | None = None, **_kwargs: Any) -> bytes:
        assert path is not None
        Image.new("RGB", (16, 12), (20, 40, 60)).save(path, format="PNG")
        return b""


class _NonChromiumSession:
    engine = "firefox"


def _read_manifest(frames_dir: Path) -> dict[str, Any]:
    return json.loads((frames_dir / "frames.json").read_text(encoding="utf-8"))


@pytest.mark.unit
class TestValidation:
    def test_bad_capture_backend(self) -> None:
        with pytest.raises(ValueError, match="capture"):
            Recorder(capture="video")  # type: ignore[arg-type]

    def test_quality_bounds(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="quality"):
            ScreencastSource(tmp_path, quality=101)
        with pytest.raises(ValueError, match="max_frames"):
            ScreencastSource(tmp_path, max_frames=0)

    def test_non_chromium_engine_fails_cleanly(self, tmp_path: Path) -> None:
        source = ScreencastSource(tmp_path)
        with pytest.raises(ScreencastUnavailable) as excinfo:
            asyncio.run(source.start(_NonChromiumSession()))  # type: ignore[arg-type]
        message = str(excinfo.value)
        assert "Chromium" in message
        assert "--capture shots" in message


@pytest.mark.unit
class TestShotsManifestUnchanged:
    async def test_default_manifest_has_no_screencast_fields(self) -> None:
        session = _ShotSession()
        step = ClickStep(selector="#btn")
        with Recorder(fps=4, default_dwell=0.25, capture="shots") as rec:
            await rec.pre_action(session)  # type: ignore[arg-type]
            result = await _ok_result()
            await rec.post_action(session, result, step)  # type: ignore[arg-type]
            rec.flush()
            manifest = _read_manifest(rec.frames_dir)

        # Exactly the pre-#303 shape: no `captured_at`, no `capture` header.
        assert set(manifest) == MANIFEST_KEYS
        assert manifest["fps"] == 4
        assert manifest["count"] == len(manifest["frames"])
        for entry in manifest["frames"]:
            assert set(entry) == SCREENSHOT_KEYS
            assert "captured_at" not in entry


@pytest.mark.unit
class TestScreencastIsPassthroughInKernel:
    def test_smooth_is_noop_and_encodes_at_video_fps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frames_dir = tmp_path / "frames"
        frames_dir.mkdir()
        Image.new("RGB", (8, 8), (0, 0, 0)).save(frames_dir / "frame-0000-000.png")
        (frames_dir / "frames.json").write_text(
            json.dumps(
                {
                    "fps": 30,
                    "count": 1,
                    "frames": [
                        {
                            "path": "frame-0000-000.png",
                            "step_index": 0,
                            "sub_index": 0,
                            "cursor_xy": None,
                            "captured_at": 0.0,
                        }
                    ],
                }
            )
        )

        import clickcast.kernel.render as render_module

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("smooth_frames_dir must not run for screencast capture")

        monkeypatch.setattr(render_module, "smooth_frames_dir", _boom)
        monkeypatch.setattr(render_module, "annotate_frames_dir", lambda *_a, **_k: 0)
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            render_module,
            "encode",
            lambda _dir, out, **kwargs: (
                seen.update(kwargs) or _fake_encode_result(Path(out), kwargs["fps"])
            ),
        )

        result = render_reel(
            frames_dir,
            RenderPlan(
                out=tmp_path / "out.mp4",
                fps=12,
                capture="screencast",
                smooth=True,
                video_fps=30,
                annotate=AnnotateConfig(clicks=False, labels=False, cursor=False),
            ),
        )

        assert result.smoothed is False
        assert result.encode_fps == 30
        assert seen["fps"] == 30


async def _ok_result() -> Any:
    class _Result:
        ok = True
        status = "ok"
        cursor_xy = (10, 10)
        error = None

    return _Result()


def _fake_encode_result(path: Path, fps: int) -> Any:
    from clickcast.encode import EncodeResult

    return EncodeResult(path=path, format="mp4", size_bytes=1, duration_s=1 / fps, frame_count=1)


@pytest.mark.unit
class TestScenarioCaptureOption:
    def test_render_capture_from_yaml(self, tmp_path: Path) -> None:
        from clickcast.scenario import ScenarioError, load

        good = tmp_path / "good.yml"
        good.write_text(
            "meta:\n  render:\n    capture: screencast\n    screencast_quality: 70\nsteps: []\n"
        )
        scenario = load(good)
        assert scenario.meta.render.capture == "screencast"
        assert scenario.meta.render.screencast_quality == 70

        bad = tmp_path / "bad.yml"
        bad.write_text("meta:\n  render:\n    capture: video\nsteps: []\n")
        with pytest.raises(ScenarioError):
            load(bad)


@pytest_asyncio.fixture
async def screencast_session(fixture_site_url: str) -> AsyncIterator[Session]:
    async with Session(viewport=(960, 600)) as sess:
        await sess.goto(f"{fixture_site_url}/slow-transition.html", wait="load")
        yield sess


@pytest.mark.integration
class TestScreencastIntegration:
    async def test_continuous_motion_and_step_attribution(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        out = tmp_path / "screencast.mp4"
        step = ClickStep(selector="#toggle", dwell=0.4)
        scenario = Scenario(
            meta=Meta.model_validate(
                {
                    "browser": {"viewport": Viewport(960, 600)},
                    "render": {"fps": 12, "capture": "screencast", "video_fps": 30},
                }
            ),
            steps=[
                GotoStep(url=f"{fixture_site_url}/slow-transition.html", wait="load"),
                step,
                step.model_copy(update={"label": "close panel"}),
            ],
        )
        builder = ReportBuilder(
            url=f"{fixture_site_url}/slow-transition.html",
            engine="chromium",
            viewport=[960, 600],
        )

        with Recorder(
            fps=12,
            default_dwell=0.4,
            capture="screencast",
            video_fps=30,
        ) as rec:
            result = await run_scenario(scenario, recorder=rec, builder=builder)
            assert result.ok
            await rec.stop_capture()  # the session is already closed; teardown is tolerant
            rec.flush()

            manifest = _read_manifest(rec.frames_dir)
            frames = manifest["frames"]
            assert manifest["capture"] == "screencast"
            assert manifest["fps"] == 30
            # The sidecar's step list still mirrors the scenario actions …
            assert [s.action for s in builder.steps] == ["goto", "click", "click"]
            assert [s.index for s in builder.steps] == [0, 1, 2]
            # … and every frame is attributed to one of those steps.
            assert {entry["step_index"] for entry in frames} <= {0, 1, 2}
            assert all(entry["step_index"] in (0, 1, 2) for entry in frames)
            assert all("captured_at" in entry for entry in frames)
            captured = [entry["captured_at"] for entry in frames]
            assert captured == sorted(captured)
            # Interval attribution gives each click step its real frames, and
            # the 300 ms click transition spans several distinct captured
            # frames at 30 fps (not one hold repeated).
            for index in (1, 2):
                per_step = [entry for entry in frames if entry["step_index"] == index]
                assert len(per_step) >= 8, (index, len(per_step))
                assert len({entry["captured_at"] for entry in per_step}) >= 8

            encode(rec.frames_dir, out, fps=30)

        stats = measure(out)
        assert stats.unique_pct >= 90, stats
        assert stats.fps == pytest.approx(30.0, abs=0.1)
        # Real-time duration: the goto, two ~0.4 s dwells and the transitions,
        # not a per-action slideshow.
        assert stats.duration_s >= 0.8

    async def test_screencast_over_shots_default_is_opt_in(
        self, screencast_session: Session
    ) -> None:
        """A recorder left on the default backend still produces stills."""
        with Recorder(fps=6, default_dwell=0.3) as rec:
            await rec.pre_action(screencast_session)
            result = await execute(ClickStep(selector="#toggle", dwell=0.2), screencast_session)
            await rec.post_action(
                screencast_session, result, ClickStep(selector="#toggle", dwell=0.2)
            )
            rec.flush()
            manifest = _read_manifest(rec.frames_dir)
        assert manifest["fps"] == 6
        assert set(manifest) == MANIFEST_KEYS
        assert all("captured_at" not in entry for entry in manifest["frames"])
