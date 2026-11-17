from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

import clickcast
from clickcast import AsyncReel, Reel, discover
from clickcast.core.actions import (
    ClickStep,
    EvaluateStep,
    GotoStep,
    HoverStep,
    ScrollStep,
    TypeStep,
    WaitForStep,
    WheelStep,
)

# ------------------------------------------------------------------
# Public exports
# ------------------------------------------------------------------


class TestPublicExports:
    def test_top_level_reel(self) -> None:
        assert Reel is clickcast.Reel

    def test_top_level_async_reel(self) -> None:
        assert AsyncReel is clickcast.AsyncReel

    def test_top_level_discover_is_the_sync_facade(self) -> None:
        # `discover` at top level MUST be the sync function, not the discovery module.
        assert callable(discover)
        assert discover is clickcast.discover
        # Sanity: taking a `url` positional arg means it's our sync facade,
        # not the async `clickcast.discovery.discover(target=Session|str)`.
        import inspect

        sig = inspect.signature(discover)
        assert "url" in sig.parameters


# ------------------------------------------------------------------
# Builders (no browser needed)
# ------------------------------------------------------------------


class TestBuilders:
    def test_chained_builders_return_self(self) -> None:
        reel = Reel("https://x", fps=6, dwell=0.5)
        assert (
            reel.goto(wait="load")
            .click("#a")
            .hover("#b")
            .type("#in", "hi")
            .press("Enter")
            .select("#s", "b")
            .scroll(by=200)
            .wait(0.1)
            .screenshot()
        ) is reel

    def test_goto_defaults_to_construction_url(self) -> None:
        reel = Reel("https://x").goto(wait="load")
        steps = reel.steps
        assert isinstance(steps[0], GotoStep)
        assert steps[0].url == "https://x"

    def test_goto_arg_overrides_construction_url(self) -> None:
        reel = Reel("https://x").goto(url="https://y", wait="load")
        assert isinstance(reel.steps[0], GotoStep)
        assert reel.steps[0].url == "https://y"

    def test_click_produces_click_step(self) -> None:
        reel = Reel("https://x").click("#btn", label="Go", dwell=1.5, optional=True)
        step = reel.steps[0]
        assert isinstance(step, ClickStep)
        assert step.selector == "#btn"
        assert step.label == "Go"
        assert step.dwell == 1.5
        assert step.optional is True

    def test_type_and_scroll_and_hover_produce_typed_steps(self) -> None:
        reel = (
            Reel("https://x").type("#input", "hello", delay=0.02).scroll(to="footer").hover("#tt")
        )
        s1, s2, s3 = reel.steps
        assert isinstance(s1, TypeStep)
        assert s1.into == "#input"
        assert s1.text == "hello"
        assert s1.delay == 0.02
        assert isinstance(s2, ScrollStep)
        assert s2.to == "footer"
        assert isinstance(s3, HoverStep)

    def test_build_scenario_reflects_meta_and_steps(self) -> None:
        reel = Reel("https://x", viewport=(800, 600), fps=6, dwell=0.5).goto(wait="load")
        s = reel.build_scenario()
        assert s.meta.viewport == "800x600"
        assert s.meta.fps == 6
        assert s.meta.dwell == 0.5
        assert len(s.steps) == 1

    def test_wait_for_produces_wait_for_step_with_defaults(self) -> None:
        reel = Reel("https://x").click(".chip").wait_for(".chip.active")
        step = reel.steps[1]
        assert isinstance(step, WaitForStep)
        assert step.selector == ".chip.active"
        assert step.state == "stable"
        assert step.quiet_ms == 200
        assert step.timeout == 10.0

    def test_wait_for_accepts_overrides_and_chains(self) -> None:
        reel = Reel("https://x").wait_for(".x", state="visible", quiet_ms=50, timeout=2.5)
        assert reel.steps[0].state == "visible"  # type: ignore[attr-defined]
        assert reel.steps[0].quiet_ms == 50  # type: ignore[attr-defined]
        assert reel.steps[0].timeout == 2.5  # type: ignore[attr-defined]
        # Must remain chainable.
        assert reel.wait_for(".y") is reel

    def test_goto_retries_kwarg_flows_into_step(self) -> None:
        reel = Reel("https://x").goto(retries=3, wait="load")
        step = reel.steps[0]
        assert isinstance(step, GotoStep)
        assert step.retries == 3

    def test_viewport_tuple_or_string_both_accepted(self) -> None:
        assert Reel("u", viewport=(800, 600)).build_scenario().meta.viewport == "800x600"
        assert Reel("u", viewport="800x600").build_scenario().meta.viewport == "800x600"
        assert Reel("u").build_scenario().meta.viewport == "1280x800"  # meta default

    def test_evaluate_and_wheel_chain(self) -> None:
        reel = (
            Reel("https://x")
            .evaluate("document.querySelector('.chart').scrollTop = 200")
            .wheel(120, selector=".canvas-zoom")
        )
        e, w = reel.steps
        assert isinstance(e, EvaluateStep)
        assert e.expression.endswith("= 200")
        assert e.args == []
        assert isinstance(w, WheelStep)
        assert w.dy == 120
        assert w.selector == ".canvas-zoom"

    def test_evaluate_with_positional_args(self) -> None:
        reel = Reel("https://x").evaluate("([a, b]) => a + b", 1, 2)
        step = reel.steps[0]
        assert isinstance(step, EvaluateStep)
        assert step.args == [1, 2]

    def test_wheel_defaults(self) -> None:
        reel = Reel("https://x").wheel(50)
        w = reel.steps[0]
        assert isinstance(w, WheelStep)
        assert w.dy == 50
        assert w.dx == 0
        assert w.selector is None

    def test_scroll_by_positional_and_selector(self) -> None:
        reel = Reel("https://x").scroll(400, selector=".chart-container", dx=15)
        step = reel.steps[0]
        assert isinstance(step, ScrollStep)
        assert step.by == 400
        assert step.selector == ".chart-container"
        assert step.dx == 15

    def test_issue_108_fluent_chain_compiles(self) -> None:
        """Acceptance criterion from #108."""
        reel = (
            Reel("https://x")
            .goto()
            .evaluate("document.querySelector('.chart').scrollTop = 200")
            .scroll(400, selector=".chart-container")
            .wheel(120, selector=".canvas-zoom")
        )
        actions = [s.action for s in reel.steps]
        assert actions == ["goto", "evaluate", "scroll", "wheel"]


# ------------------------------------------------------------------
# Sync safety
# ------------------------------------------------------------------


class TestAssertionsIntegration:
    """`Reel.assertions()` + `Reel.assertions_diff()` — no browser needed.

    Uses the same fixture-report trick as the assertions-module tests:
    seed the Reel's cached report directly, then exercise the public
    surface. Avoids launching Playwright while still covering the
    Reel-level wiring (RuntimeError before save, distillation shape,
    on-disk baseline diff).
    """

    def _seed(self, reel: Reel) -> None:
        from clickcast.feedback import Media, PageState, Report, StepReport

        reel._last_report = Report(
            clickcast_version="0.1.3",
            started_at="2026-07-23T15:00:00+00:00",
            duration_s=1.0,
            media=Media(
                path="tour.gif",
                format="gif",
                size_bytes=1024,
                frame_count=4,
                duration_s=0.5,
                fps=8,
            ),
            steps=[
                StepReport(
                    index=0,
                    action="goto",
                    args={"url": "https://x"},
                    status="ok",
                    duration_ms=100.0,
                    frames=["frame-0000-000.png"],
                    label="Open site",
                    page_state=PageState(),
                ),
                StepReport(
                    index=1,
                    action="click",
                    args={"selector": "#cta"},
                    status="ok",
                    duration_ms=50.0,
                    frames=["frame-0001-000.png"],
                    label="Click CTA",
                    page_state=PageState(),
                ),
            ],
        )

    def test_assertions_before_save_raises(self) -> None:
        reel = Reel("https://x").goto(wait="load")
        with pytest.raises(RuntimeError, match=r"requires a completed save"):
            reel.assertions()

    def test_assertions_returns_distilled_shape(self) -> None:
        reel = Reel("https://x").goto(wait="load")
        self._seed(reel)
        out = reel.assertions()
        assert out["schema_version"] == 1
        assert out["step_count"] == 2
        assert [s["action"] for s in out["steps"]] == ["goto", "click"]

    def test_assertions_diff_against_matching_baseline_is_clean(self, tmp_path: Path) -> None:
        reel = Reel("https://x").goto(wait="load")
        self._seed(reel)
        baseline = tmp_path / "golden.json"
        baseline.write_text(json.dumps(reel.assertions(), indent=2))
        drift, clean = reel.assertions_diff(baseline)
        assert clean is True
        assert drift == []

    def test_assertions_diff_reports_drift_from_baseline(self, tmp_path: Path) -> None:
        reel = Reel("https://x").goto(wait="load")
        self._seed(reel)
        baseline = tmp_path / "golden.json"
        # Baseline claims 3 steps; current has 2 — expect a step_count drift.
        payload = reel.assertions()
        payload["step_count"] = 3
        payload["steps"].append(
            {
                "action": "scroll",
                "label": None,
                "status": "ok",
                "console_error_count": 0,
                "page_error_count": 0,
                "network_failed_count": 0,
            }
        )
        baseline.write_text(json.dumps(payload, indent=2))
        drift, clean = reel.assertions_diff(baseline)
        assert clean is False
        assert any("step_count" in d for d in drift)


class TestSyncSafety:
    async def test_reel_save_from_loop_raises_helpful_error(self) -> None:
        reel = Reel("https://x").goto(wait="load")
        with pytest.raises(RuntimeError, match="AsyncReel"):
            reel.save("out.gif")

    async def test_top_level_discover_from_loop_raises(self) -> None:
        with pytest.raises(RuntimeError, match=r"clickcast\.discovery"):
            discover("https://x")


# ------------------------------------------------------------------
# Integration — real chromium against data: URLs
# ------------------------------------------------------------------


_FIXTURE_URL = (
    "data:text/html,<html><body><h1>hi</h1><button id=btn>Click me</button></body></html>"
)


@pytest.mark.integration
class TestReelIntegration:
    def test_sync_reel_save_writes_gif_and_sidecar(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.gif"
        result = (
            Reel(_FIXTURE_URL, viewport=(400, 300), fps=4, dwell=0.25)
            .goto(wait="load")
            .click("#btn", dwell=0.25, label="Click")
            .save(out)
        )
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 500
        with Image.open(out) as img:
            assert img.n_frames >= 2
        sidecar = out.with_suffix(out.suffix + ".json")
        assert sidecar.exists()
        payload = json.loads(sidecar.read_text())
        assert payload["media"]["format"] == "gif"

    def test_sync_reel_save_twice_produces_independent_reels(self, tmp_path: Path) -> None:
        reel = (
            Reel(_FIXTURE_URL, viewport=(400, 300), fps=4, dwell=0.25)
            .goto(wait="load")
            .click("#btn", dwell=0.25)
        )
        a = tmp_path / "a.gif"
        b = tmp_path / "b.gif"
        reel.save(a)
        reel.save(b)
        assert a.exists() and b.exists()
        assert a.stat().st_size > 500 and b.stat().st_size > 500

    def test_no_sidecar_flag_skips_sidecar(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.gif"
        Reel(_FIXTURE_URL, viewport=(400, 300), fps=4, dwell=0.25).goto(wait="load").click(
            "#btn", dwell=0.25
        ).save(out, no_sidecar=True)
        assert out.exists()
        assert not out.with_suffix(out.suffix + ".json").exists()

    def test_top_level_discover_returns_elements(self) -> None:
        elements = discover(_FIXTURE_URL, viewport=(400, 300), limit=5)
        assert elements
        assert any("btn" in e.selector.lower() or e.text == "Click me" for e in elements)


@pytest.mark.integration
class TestAsyncReelIntegration:
    async def test_async_reel_save(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.gif"
        result = await (
            AsyncReel(_FIXTURE_URL, viewport=(400, 300), fps=4, dwell=0.25)
            .goto(wait="load")
            .click("#btn", dwell=0.25)
            .save(out)
        )
        assert result == out
        assert out.exists()
        with Image.open(out) as img:
            assert img.n_frames >= 2


# ------------------------------------------------------------------
# Render pipeline (#261) — SDK goes through clickcast.kernel.render
# ------------------------------------------------------------------


class _FakeRecorder:
    """Stand-in for :class:`clickcast.capture.Recorder` in pipeline tests.

    The real recorder needs a browser; the render stage only needs a frames
    directory with a ``frames.json`` manifest, so the fake just hands one back.
    """

    def __init__(self, frames_dir: Path, **_kwargs: object) -> None:
        self.frames_dir = frames_dir

    def __enter__(self) -> _FakeRecorder:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def flush(self) -> None:
        return None


def _write_frame_dir(root: Path, *, count: int = 6) -> Path:
    """Synthetic recorder output: PNG frames + ``frames.json`` manifest."""
    from PIL import ImageDraw

    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for i in range(count):
        img = Image.new("RGB", (160, 100), (20 + i * 8, 70, 140))
        ImageDraw.Draw(img).rectangle([12 + i * 4, 12, 68 + i * 4, 52], fill=(250, 250, 250))
        name = f"frame-{i:04d}-000.png"
        img.save(root / name)
        entries.append(
            {
                "path": name,
                "step_index": i // 2,
                "sub_index": i % 2,
                "cursor_xy": [50 + i * 4, 40] if i % 2 else None,
            }
        )
    (root / "frames.json").write_text(json.dumps({"frames": entries}))
    return root


def _install_fake_run(monkeypatch: pytest.MonkeyPatch, frames_dir: Path) -> None:
    """Point ``reel._run_and_encode``'s recorder/runner at synthetic frames."""
    import clickcast.reel as reel_mod
    from clickcast.core.actions import ActionResult
    from clickcast.scenario.scenario import RunResult

    monkeypatch.setattr(reel_mod, "Recorder", lambda **_kw: _FakeRecorder(frames_dir))

    async def fake_run_scenario(
        scenario: object, *, recorder: object = None, builder: object = None
    ):
        return RunResult(
            results=[
                ActionResult(ok=True, status="ok", action="goto", cursor_xy=(50, 40)),
                ActionResult(ok=True, status="ok", action="click", cursor_xy=(56, 40)),
            ],
            failed_at=None,
        )

    monkeypatch.setattr(reel_mod, "run_scenario", fake_run_scenario)


_SCENARIO_URL = "https://example.com"


def _pipeline_scenario() -> object:
    return (
        Reel(_SCENARIO_URL, fps=12)
        .goto(wait="load", label="Open")
        .click("#btn", label="Click")
        .build_scenario()
    )


class TestRenderPipeline:
    async def test_default_save_is_byte_identical_to_direct_encode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No opt-in flags → the encoded reel must not change (#261 risk)."""
        from clickcast.encode import encode
        from clickcast.reel import _run_and_encode

        frames = _write_frame_dir(tmp_path / "frames")
        pristine = tmp_path / "pristine"
        shutil.copytree(frames, pristine)
        _install_fake_run(monkeypatch, frames)

        out = tmp_path / "sdk.gif"
        _result, enc, encode_fps = await _run_and_encode(
            _pipeline_scenario(),  # type: ignore[arg-type]
            out,
            format_="gif",
            quality=8,
            loop=0,
        )
        reference = tmp_path / "reference.gif"
        encode(pristine, reference, fps=12, quality=8, loop=0, format="gif")

        assert out.read_bytes() == reference.read_bytes()
        assert enc.frame_count == 6
        assert encode_fps == 12

    async def test_default_does_not_interpolate_or_annotate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from unittest.mock import patch

        from clickcast.reel import _run_and_encode

        frames = _write_frame_dir(tmp_path / "frames")
        _install_fake_run(monkeypatch, frames)

        with (
            patch("clickcast.kernel.render.interpolate_cursor_motion") as interpolate,
            patch("clickcast.kernel.render.annotate_frames_dir", return_value=0) as annotate,
        ):
            await _run_and_encode(
                _pipeline_scenario(),  # type: ignore[arg-type]
                tmp_path / "out.gif",
                format_="gif",
                quality=8,
                loop=0,
            )

        interpolate.assert_not_called()
        config = annotate.call_args.kwargs["config"]
        assert config.clicks is False
        assert config.labels is False
        assert config.cursor is False
        assert config.progress is False
        assert config.actions_panel is False
        assert annotate.call_args.kwargs["steps"] is None

    async def test_smooth_encodes_at_video_fps(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from clickcast.encode import EncodeResult
        from clickcast.reel import _run_and_encode

        frames = _write_frame_dir(tmp_path / "frames")
        _install_fake_run(monkeypatch, frames)
        out = tmp_path / "smooth.gif"
        fake_enc = EncodeResult(path=out, format="gif", size_bytes=1, duration_s=1.0, frame_count=8)
        smoothed = SimpleNamespace(output_fps=24, inserted=2, total_frames=8)

        with (
            patch("clickcast.kernel.render.smooth_frames_dir", return_value=smoothed) as smooth,
            patch("clickcast.kernel.render.encode", return_value=fake_enc) as encode,
        ):
            _result, _enc, encode_fps = await _run_and_encode(
                _pipeline_scenario(),  # type: ignore[arg-type]
                out,
                format_="gif",
                quality=8,
                loop=0,
                smooth=True,
                video_fps=24,
            )

        assert smooth.call_args.kwargs["source_fps"] == 12
        assert encode.call_args.kwargs["fps"] == 24
        assert encode_fps == 24

    async def test_annotate_produces_frame_visible_overlays(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from clickcast.annotate import AnnotateConfig
        from clickcast.reel import _run_and_encode

        frames = _write_frame_dir(tmp_path / "frames")
        pristine = tmp_path / "pristine"
        shutil.copytree(frames, pristine)
        _install_fake_run(monkeypatch, frames)

        await _run_and_encode(
            _pipeline_scenario(),  # type: ignore[arg-type]
            tmp_path / "annotated.gif",
            format_="gif",
            quality=8,
            loop=0,
            annotate=AnnotateConfig(),
        )

        before = Image.open(pristine / "frame-0000-000.png").convert("RGB")
        after = Image.open(frames / "frame-0000-000.png").convert("RGB")
        assert before.tobytes() != after.tobytes()

    async def test_annotate_interpolates_cursor_motion(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from unittest.mock import patch

        from clickcast.annotate import AnnotateConfig
        from clickcast.reel import _run_and_encode

        frames = _write_frame_dir(tmp_path / "frames")
        _install_fake_run(monkeypatch, frames)

        with patch("clickcast.kernel.render.interpolate_cursor_motion", return_value=0) as interp:
            await _run_and_encode(
                _pipeline_scenario(),  # type: ignore[arg-type]
                tmp_path / "out.gif",
                format_="gif",
                quality=8,
                loop=0,
                annotate=AnnotateConfig(),
            )
        interp.assert_called_once()

    async def test_step_annotations_carry_labels_and_ripples(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from unittest.mock import patch

        from clickcast.annotate import AnnotateConfig
        from clickcast.reel import _run_and_encode

        frames = _write_frame_dir(tmp_path / "frames")
        _install_fake_run(monkeypatch, frames)

        with patch("clickcast.kernel.render.annotate_frames_dir", return_value=0) as annotate:
            await _run_and_encode(
                _pipeline_scenario(),  # type: ignore[arg-type]
                tmp_path / "out.gif",
                format_="gif",
                quality=8,
                loop=0,
                annotate=AnnotateConfig(),
            )

        steps = annotate.call_args.kwargs["steps"]
        # 6 frames → step_index 0..2; labels come from the scenario's steps.
        assert steps[0].label == "Open"
        assert steps[1].label == "Click"
        assert steps[1].click_at == (56, 40)

    def test_save_forwards_render_options(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`save` plumbs annotate/smooth/video_fps into `_run_and_encode`."""
        import clickcast.reel as reel_mod
        from clickcast.annotate import AnnotateConfig
        from clickcast.encode import EncodeResult

        out = tmp_path / "out.gif"
        fake_enc = EncodeResult(path=out, format="gif", size_bytes=1, duration_s=1.0, frame_count=2)
        captured: dict[str, object] = {}
        config = AnnotateConfig()

        async def fake_run_and_encode(scenario, out_path, **kwargs):
            captured.update(kwargs)
            captured["out"] = out_path
            captured["scenario"] = scenario
            from types import SimpleNamespace

            return SimpleNamespace(ok=True, failed_at=None), fake_enc, 24

        monkeypatch.setattr(reel_mod, "_run_and_encode", fake_run_and_encode)

        result = (
            Reel(_SCENARIO_URL, fps=12)
            .goto(wait="load")
            .save(out, annotate=config, smooth=True, video_fps=24, with_feedback=True)
        )

        assert result == out
        assert captured["annotate"] is config
        assert captured["smooth"] is True
        assert captured["video_fps"] == 24

    def test_video_fps_is_validated(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="video_fps"):
            Reel(_SCENARIO_URL).goto(wait="load").save(tmp_path / "o.gif", video_fps=0)


class TestBuilderTimeoutMs:
    """`timeout_ms` reaches the built step for every action that honors it."""

    @pytest.mark.parametrize(
        "build_action",
        [
            lambda r: Reel(r).goto(timeout_ms=2000),
            lambda r: Reel(r).click("#a", timeout_ms=2000),
            lambda r: Reel(r).dblclick("#a", timeout_ms=2000),
            lambda r: Reel(r).hover("#a", timeout_ms=2000),
            lambda r: Reel(r).type("#a", "x", timeout_ms=2000),
            lambda r: Reel(r).press("Enter", timeout_ms=2000),
            lambda r: Reel(r).select("#a", "b", timeout_ms=2000),
            lambda r: Reel(r).scroll(by=100, timeout_ms=2000),
            lambda r: Reel(r).wheel(50, timeout_ms=2000),
        ],
    )
    def test_timeout_ms_flows_into_step(self, build_action) -> None:
        step = build_action("https://x").steps[0]
        assert step.timeout_ms == 2000

    def test_timeout_ms_defaults_to_none(self) -> None:
        assert Reel("https://x").click("#a").steps[0].timeout_ms is None


@pytest.mark.integration
class TestReelPipelineIntegration:
    """End-to-end checks against a real browser + data: URL."""

    def test_last_report_populated_without_sidecar(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.gif"
        reel = (
            Reel(_FIXTURE_URL, viewport=(400, 300), fps=4, dwell=0.25)
            .goto(wait="load")
            .click("#btn", dwell=0.25, label="Click")
        )
        reel.save(out, no_sidecar=True)
        assert not out.with_suffix(out.suffix + ".json").exists()
        assert reel._last_report is not None
        assert reel.assertions()["step_count"] == 2

    def test_with_feedback_adds_pointer_block(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.gif"
        Reel(_FIXTURE_URL, viewport=(400, 300), fps=4, dwell=0.25).goto(wait="load").save(
            out, with_feedback=True
        )
        payload = json.loads(out.with_suffix(out.suffix + ".json").read_text())
        assert payload["feedback"] is not None

    def test_smooth_reel_reports_video_fps(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.gif"
        Reel(_FIXTURE_URL, viewport=(400, 300), fps=4, dwell=0.25).goto(wait="load").save(
            out, smooth=True, video_fps=20
        )
        with Image.open(out) as img:
            # Pillow's GIF optimizer fuses identical hold slots into one frame
            # with a summed duration, so assert the per-frame quantum instead
            # of the raw frame count.
            assert img.info["duration"] % round(1000 / 20) == 0
        payload = json.loads(out.with_suffix(out.suffix + ".json").read_text())
        assert payload["media"]["fps"] == 20


@pytest.mark.unit
def test_scenario_step_labels_expand_repeat() -> None:
    """Reel-grammar captions are indexed per recorder step, not per scenario step."""
    from clickcast.reel import _scenario_step_labels
    from clickcast.scenario.scenario import loads

    scenario = loads(
        """
steps:
  - goto: https://example.test/
    label: Open home
  - click: "#a"
    label: Open A
    repeat: 3
  - click: "#b"
"""
    )

    assert _scenario_step_labels(scenario) == ("Open home", "Open A", "Open A", "Open A", None)
