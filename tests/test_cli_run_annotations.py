"""`_do_run` should annotate scenario reels — same overlays as `auto`.

Ships #83. `_scenario_step_annotations` builds the step-index → annotation
map from the completed scenario run so `annotate_frames_dir` can render
per-step labels + click ripples on scenario-driven tours (previously they
were plain unannotated screenshots).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

from clickcast.cli import _scenario_step_annotations


@dataclass
class _FakeStep:
    action: str
    label: str | None = None
    repeat: int = 1
    selector: str | None = None
    into: str | None = None
    url: str | None = None


@dataclass
class _FakeScenario:
    steps: list[_FakeStep] = field(default_factory=list)


def _mk_result(ok: bool = True, cursor: tuple[int, int] | None = (100, 80)) -> MagicMock:
    r = MagicMock()
    r.ok = ok
    r.status = "ok" if ok else "failed"
    r.cursor_xy = cursor
    return r


class TestScenarioStepAnnotations:
    def test_labels_flow_through(self) -> None:
        scenario = _FakeScenario(
            steps=[
                _FakeStep(action="goto", url="https://x", label="open"),
                _FakeStep(action="click", selector="#save", label="save it"),
            ]
        )
        result = MagicMock()
        result.results = [_mk_result(), _mk_result()]
        out = _scenario_step_annotations(scenario, result)
        assert out[0].label == "open"
        assert out[1].label == "save it"

    def test_missing_label_synthesized_from_action_and_selector(self) -> None:
        scenario = _FakeScenario(
            steps=[
                _FakeStep(action="click", selector="#save"),
                _FakeStep(action="type", into="input[name=q]"),
                _FakeStep(action="goto", url="https://x"),
                _FakeStep(action="scroll"),
            ]
        )
        result = MagicMock()
        result.results = [_mk_result() for _ in range(4)]
        out = _scenario_step_annotations(scenario, result)
        assert out[0].label == "click: #save"
        assert out[1].label == "type: input[name=q]"
        assert out[2].label == "goto: https://x"
        assert out[3].label == "scroll"

    def test_click_ripple_only_on_click_actions(self) -> None:
        scenario = _FakeScenario(
            steps=[
                _FakeStep(action="goto", url="https://x"),
                _FakeStep(action="click", selector="#a"),
                _FakeStep(action="dblclick", selector="#b"),
                _FakeStep(action="scroll"),
                _FakeStep(action="type", into="#i"),
            ]
        )
        result = MagicMock()
        result.results = [_mk_result() for _ in range(5)]
        out = _scenario_step_annotations(scenario, result)
        assert out[0].click_at is None
        assert out[1].click_at == (100, 80)
        assert out[2].click_at == (100, 80)
        assert out[3].click_at is None
        assert out[4].click_at is None

    def test_failed_click_gets_no_ripple(self) -> None:
        scenario = _FakeScenario(steps=[_FakeStep(action="click", selector="#missing")])
        result = MagicMock()
        result.results = [_mk_result(ok=False)]
        out = _scenario_step_annotations(scenario, result)
        assert out[0].click_at is None

    def test_repeat_expands_to_multiple_annotations(self) -> None:
        scenario = _FakeScenario(
            steps=[
                _FakeStep(action="click", selector="#x", label="poke", repeat=3),
                _FakeStep(action="scroll", label="scroll"),
            ]
        )
        result = MagicMock()
        result.results = [_mk_result() for _ in range(4)]
        out = _scenario_step_annotations(scenario, result)
        assert len(out) == 4
        assert out[0].label == "poke"
        assert out[1].label == "poke"
        assert out[2].label == "poke"
        assert out[3].label == "scroll"

    def test_early_failure_stops_at_failed_step(self) -> None:
        scenario = _FakeScenario(
            steps=[
                _FakeStep(action="goto", url="https://x"),
                _FakeStep(action="click", selector="#save"),
                _FakeStep(action="click", selector="#never"),
            ]
        )
        result = MagicMock()
        result.results = [_mk_result(), _mk_result(ok=False)]
        out = _scenario_step_annotations(scenario, result)
        assert len(out) == 2
        assert 0 in out and 1 in out
        assert 2 not in out

    def test_empty_scenario_returns_empty_map(self) -> None:
        scenario = _FakeScenario(steps=[])
        result = MagicMock()
        result.results = []
        out = _scenario_step_annotations(scenario, result)
        assert out == {}
