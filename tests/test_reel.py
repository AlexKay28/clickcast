from __future__ import annotations

import json
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
