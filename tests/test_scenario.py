from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from clickcast.capture import Recorder
from clickcast.core.actions import ClickStep, GotoStep
from clickcast.core.session import Session
from clickcast.scenario import Meta, Scenario, ScenarioError, load, loads, run
from clickcast.scenario.scenario import _normalize_step, _substitute_vars


class TestLoadBasic:
    def test_minimal(self) -> None:
        s = loads("steps: []")
        assert isinstance(s, Scenario)
        assert isinstance(s.meta, Meta)
        assert s.steps == []

    def test_empty_string(self) -> None:
        s = loads("")
        assert s.steps == []

    def test_meta_defaults(self) -> None:
        s = loads("meta:\n  name: hello\nsteps: []")
        assert s.meta.name == "hello"
        assert s.meta.engine == "chromium"
        assert s.meta.fps == 12
        assert s.meta.viewport == "1280x800"

    def test_full_meta_block(self) -> None:
        yaml_text = """
        meta:
          name: t
          engine: firefox
          viewport: 800x600
          fps: 24
          dwell: 0.5
          format: mp4
          out: t.mp4
          lang: en
          dark: true
        steps: []
        """
        s = loads(yaml_text)
        assert s.meta.engine == "firefox"
        assert s.meta.viewport == "800x600"
        assert s.meta.fps == 24
        assert s.meta.dwell == 0.5
        assert s.meta.dark is True

    def test_unknown_meta_key_raises(self) -> None:
        with pytest.raises(ScenarioError):
            loads("meta:\n  nonsense: 1\nsteps: []")


class TestNormalizeStep:
    def test_goto_string_url(self) -> None:
        n = _normalize_step({"goto": "https://x", "wait": "load", "label": "L"}, 0)
        assert n == {"action": "goto", "url": "https://x", "wait": "load", "label": "L"}

    def test_click_selector_string(self) -> None:
        n = _normalize_step({"click": "text=Compare"}, 0)
        assert n == {"action": "click", "selector": "text=Compare"}

    def test_type_dict_value(self) -> None:
        n = _normalize_step({"type": {"into": "#q", "text": "hi"}}, 0)
        assert n == {"action": "type", "into": "#q", "text": "hi"}

    def test_press_string_value(self) -> None:
        n = _normalize_step({"press": "Enter"}, 0)
        assert n == {"action": "press", "key": "Enter"}

    def test_select_maps_in_to_into(self) -> None:
        n = _normalize_step({"select": {"in": "#m", "value": "GDP"}}, 0)
        assert n == {"action": "select", "into": "#m", "value": "GDP"}

    def test_scroll_by_pixels(self) -> None:
        n = _normalize_step({"scroll": {"by": 400}}, 0)
        assert n == {"action": "scroll", "by": 400}

    def test_wait_polymorphic(self) -> None:
        assert _normalize_step({"wait": 1.5}, 0) == {"action": "wait", "wait": 1.5}
        assert _normalize_step({"wait": "networkidle"}, 0) == {
            "action": "wait",
            "wait": "networkidle",
        }
        assert _normalize_step({"wait": ".sel"}, 0) == {"action": "wait", "wait": ".sel"}

    def test_screenshot_with_options(self) -> None:
        n = _normalize_step({"screenshot": {"full_page": True}}, 0)
        assert n == {"action": "screenshot", "full_page": True}

    def test_common_fields_carry_through(self) -> None:
        n = _normalize_step(
            {"click": "#x", "dwell": 2.0, "optional": True, "repeat": 3, "label": "l"}, 0
        )
        assert n["dwell"] == 2.0
        assert n["optional"] is True
        assert n["repeat"] == 3
        assert n["label"] == "l"

    def test_no_action_raises(self) -> None:
        with pytest.raises(ScenarioError, match="expected exactly one action"):
            _normalize_step({"label": "x"}, 0)

    def test_two_actions_raises(self) -> None:
        with pytest.raises(ScenarioError, match="expected exactly one action"):
            _normalize_step({"click": "#a", "goto": "http://x"}, 0)


class TestLoadSteps:
    def test_parses_readme_scenario(self) -> None:
        yaml_text = """
        meta:
          name: WorldSight
          out: worldsight.gif
        steps:
          - goto: https://example.com
            wait: networkidle
            label: "Open"
          - click: "text=3D"
            label: "3D"
            dwell: 2.0
          - hover: "[aria-label='Rankings']"
          - click: "[aria-label='Rankings']"
            label: Open Rankings
          - scroll:
              to: footer
          - screenshot:
              full_page: false
        """
        s = loads(yaml_text)
        assert [step.action for step in s.steps] == [
            "goto",
            "click",
            "hover",
            "click",
            "scroll",
            "screenshot",
        ]
        assert isinstance(s.steps[0], GotoStep)
        assert s.steps[0].url == "https://example.com"
        assert s.steps[1].dwell == 2.0
        assert isinstance(s.steps[1], ClickStep)
        assert s.steps[1].selector == "text=3D"

    def test_missing_required_field_error_is_readable(self) -> None:
        # click without selector value is impossible in canonical YAML, but a
        # type step without `into` is a common mistake.
        with pytest.raises(ScenarioError, match="step 0"):
            loads("steps:\n  - type:\n      text: hi\n")

    def test_invalid_yaml_syntax(self) -> None:
        with pytest.raises(ScenarioError, match="YAML syntax"):
            loads("steps:\n  - click: [unclosed\n")

    def test_top_level_must_be_mapping(self) -> None:
        with pytest.raises(ScenarioError, match="mapping"):
            loads("- 1\n- 2\n")

    def test_load_from_file(self, tmp_path: Path) -> None:
        p = tmp_path / "tour.yml"
        p.write_text("steps:\n  - goto: https://x\n    wait: load\n")
        s = load(p)
        assert len(s.steps) == 1
        assert s.steps[0].action == "goto"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ScenarioError, match="not found"):
            load(tmp_path / "missing.yml")


class TestVariableSubstitution:
    def test_substitute_string(self) -> None:
        assert _substitute_vars("Hello {{ name }}", {"name": "world"}) == "Hello world"

    def test_substitute_recurses_into_dict_and_list(self) -> None:
        obj = {"a": "{{ x }}", "b": ["{{ y }}", {"c": "{{ z }}"}]}
        out = _substitute_vars(obj, {"x": "X", "y": "Y", "z": "Z"})
        assert out == {"a": "X", "b": ["Y", {"c": "Z"}]}

    def test_undefined_variable_raises(self) -> None:
        with pytest.raises(ScenarioError, match="undefined variable"):
            _substitute_vars("{{ missing }}", {})

    def test_load_with_vars(self) -> None:
        s = loads(
            'steps:\n  - goto: "{{ base }}/{{ page }}"\n    wait: load\n',
            variables={"base": "https://x.com", "page": "home"},
        )
        assert s.steps[0].url == "https://x.com/home"  # type: ignore[attr-defined]

    def test_no_filters_supported(self) -> None:
        # `{{ x|upper }}` is a Jinja filter — we don't support them, so the
        # regex won't match and the literal stays untouched.
        out = _substitute_vars("{{ x|upper }}", {"x": "y"})
        assert out == "{{ x|upper }}"


class TestPerformance:
    def test_100_step_scenario_parses_under_100ms(self) -> None:
        # Roadmap acceptance: 100-step scenario parses in < 100ms
        yaml_text = "steps:\n" + "\n".join(f'  - click: "#btn-{i}"' for i in range(100))
        t0 = time.perf_counter()
        s = loads(yaml_text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert len(s.steps) == 100
        assert elapsed_ms < 100, f"took {elapsed_ms:.1f}ms"


# -----------------------------------------------------------------
# Integration: run() end-to-end
# -----------------------------------------------------------------


FIXTURE_HTML = """<!DOCTYPE html>
<html><body>
  <button id="btn1">Click me</button>
  <div id="marker"></div>
  <script>
    document.getElementById('btn1').addEventListener('click', () => {
      document.getElementById('marker').textContent = 'clicked';
    });
  </script>
</body></html>
"""


@pytest_asyncio.fixture
async def loaded_session() -> AsyncIterator[Session]:
    async with Session(viewport=(600, 400)) as sess:
        await sess.page.set_content(FIXTURE_HTML)
        sess.page.set_default_timeout(3000)
        yield sess


@pytest.mark.integration
class TestRunIntegration:
    async def test_runs_two_steps_against_open_session(self, loaded_session: Session) -> None:
        yaml_text = """
        steps:
          - click: "#btn1"
            label: click
        """
        scenario = loads(yaml_text)
        result = await run(scenario, session=loaded_session)
        assert result.ok
        assert result.failed_at is None
        assert len(result.results) == 1
        # DOM side-effect proves the step actually ran
        marker = await loaded_session.page.locator("#marker").text_content()
        assert marker == "clicked"

    async def test_stops_on_first_failure(self, loaded_session: Session) -> None:
        yaml_text = """
        steps:
          - click: "#btn1"
          - click: "#nope"
          - click: "#btn1"
        """
        scenario = loads(yaml_text)
        result = await run(scenario, session=loaded_session)
        assert not result.ok
        assert result.failed_at == 1
        assert len(result.results) == 2  # third step never ran

    async def test_optional_failure_is_absorbed(self, loaded_session: Session) -> None:
        yaml_text = """
        steps:
          - click: "#btn1"
          - click: "#nope"
            optional: true
          - click: "#btn1"
        """
        scenario = loads(yaml_text)
        result = await run(scenario, session=loaded_session)
        assert result.ok
        assert [r.status for r in result.results] == ["ok", "skipped", "ok"]

    async def test_recorder_captures_frames(self, loaded_session: Session, tmp_path: Path) -> None:
        yaml_text = """
        steps:
          - click: "#btn1"
            dwell: 0.25
        """
        scenario = loads(yaml_text)
        with Recorder(fps=4, default_dwell=0.25) as rec:
            result = await run(scenario, session=loaded_session, recorder=rec)
            assert result.ok
            # 1 pre + round(0.25 * 4) = 1 post = 2 frames per step
            assert len(rec.frames) == 2

    async def test_repeat_honored_at_runner_layer(self, loaded_session: Session) -> None:
        yaml_text = """
        steps:
          - click: "#btn1"
            repeat: 3
        """
        scenario = loads(yaml_text)
        result = await run(scenario, session=loaded_session)
        assert result.ok
        # One ActionResult per repeat iteration
        assert len(result.results) == 3
