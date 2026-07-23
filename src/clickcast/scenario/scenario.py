"""YAML scenario parser + runner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from clickcast.core.actions import ActionResult, Step, execute
from clickcast.core.session import Session

if TYPE_CHECKING:
    from clickcast.capture import Recorder


__all__ = [
    "Meta",
    "RunResult",
    "Scenario",
    "ScenarioError",
    "load",
    "run",
]


class ScenarioError(Exception):
    """Raised when a scenario file can't be loaded or validated."""


# ------- Models --------------------------------------------------------------


class Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    engine: str = "chromium"
    viewport: str | None = "1280x800"
    device: str | None = None
    fps: int = 12
    dwell: float = 1.0
    format: str = "gif"
    out: str = "reel.gif"
    lang: str | None = None
    dark: bool = False
    headful: bool = False
    slowmo: int = 0
    proxy: str | None = None
    # Free-form until #8 defines AnnotateConfig
    annotate: dict[str, Any] | None = None
    # Optional include-parent path — deferred implementation per roadmap
    extends: str | None = None


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: Meta = Field(default_factory=Meta)
    steps: list[Step] = Field(default_factory=list)


@dataclass(slots=True, frozen=True)
class RunResult:
    results: list[ActionResult]
    failed_at: int | None  # step index of the first failing step, or None

    @property
    def ok(self) -> bool:
        return self.failed_at is None


# ------- YAML → canonical Step ----------------------------------------------

_ACTION_KEYS = {
    "goto", "click", "dblclick", "hover", "type", "press",
    "select", "scroll", "wait", "screenshot",
}  # fmt: skip

_COMMON_KEYS = {"label", "dwell", "optional", "repeat"}

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _substitute_vars(obj: Any, variables: dict[str, str]) -> Any:
    """Recursively replace `{{ name }}` placeholders. Raises on undefined names."""

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise ScenarioError(f"undefined variable {{{{ {name} }}}}")
        return str(variables[name])

    if isinstance(obj, str):
        return _VAR_RE.sub(repl, obj)
    if isinstance(obj, dict):
        return {k: _substitute_vars(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_vars(v, variables) for v in obj]
    return obj


def _normalize_step(raw: Any, index: int) -> dict[str, Any]:
    """Turn the YAML shape `{action_verb: value, ...common}` into the canonical
    `{"action": verb, <primary>: value, ...common}` accepted by pydantic."""

    if not isinstance(raw, dict):
        raise ScenarioError(f"step {index}: expected a mapping, got {type(raw).__name__}")

    action_keys = [k for k in raw if k in _ACTION_KEYS]
    # `wait` can be either its own step OR a per-step field (e.g. goto+wait).
    # When another action verb is present, treat `wait` as the per-step field.
    if len(action_keys) > 1 and "wait" in action_keys:
        action_keys = [k for k in action_keys if k != "wait"]
    if len(action_keys) != 1:
        raise ScenarioError(
            f"step {index}: expected exactly one action verb "
            f"(one of {sorted(_ACTION_KEYS)}); got {action_keys}"
        )
    action = action_keys[0]
    value = raw[action]

    canonical: dict[str, Any] = {"action": action}
    for key in _COMMON_KEYS:
        if key in raw:
            canonical[key] = raw[key]

    if action == "goto":
        if isinstance(value, str):
            canonical["url"] = value
        elif isinstance(value, dict):
            canonical.update(value)
        else:
            raise ScenarioError(f"step {index}: goto value must be a URL or mapping")
        # `wait` may appear either as a top-level step field or inside the goto value
        if "wait" in raw:
            canonical["wait"] = raw["wait"]

    elif action in {"click", "dblclick", "hover"}:
        if not isinstance(value, str):
            raise ScenarioError(f"step {index}: {action} value must be a selector string")
        canonical["selector"] = value

    elif action == "type":
        if not isinstance(value, dict):
            raise ScenarioError(f"step {index}: type value must be a mapping (into/text/delay)")
        canonical.update(value)

    elif action == "press":
        if isinstance(value, str):
            canonical["key"] = value
        elif isinstance(value, dict):
            canonical.update(value)
        else:
            raise ScenarioError(f"step {index}: press value must be a key string or mapping")

    elif action == "select":
        if not isinstance(value, dict):
            raise ScenarioError(f"step {index}: select value must be a mapping (in/value)")
        v = dict(value)
        # Convention: YAML uses `in:`, canonical is `into`
        if "in" in v:
            v["into"] = v.pop("in")
        canonical.update(v)

    elif action == "scroll":
        if not isinstance(value, dict):
            raise ScenarioError(f"step {index}: scroll value must be a mapping (to/by)")
        canonical.update(value)

    elif action == "wait":
        canonical["wait"] = value

    elif action == "screenshot":
        if isinstance(value, dict):
            canonical.update(value)
        elif not isinstance(value, bool | int | str | type(None)):
            raise ScenarioError(f"step {index}: screenshot value must be a mapping or scalar")
        # bare `screenshot:` (with no options) is fine — nothing to merge

    return canonical


_STEP_ADAPTER: TypeAdapter[Any] = TypeAdapter(Step)


def _validate_steps(canonical: list[dict[str, Any]], path: Path | None) -> list[Step]:
    steps: list[Step] = []
    for i, raw in enumerate(canonical):
        try:
            steps.append(cast("Step", _STEP_ADAPTER.validate_python(raw)))
        except ValidationError as e:
            location = f"{path}:" if path else ""
            raise ScenarioError(f"{location}step {i}: {e}") from e
    return steps


# ------- Public: load --------------------------------------------------------


def load(
    path: Path | str,
    *,
    variables: dict[str, str] | None = None,
) -> Scenario:
    """Load and validate a YAML scenario. Raises ScenarioError on failure."""

    p = Path(path)
    try:
        text = p.read_text()
    except FileNotFoundError as e:
        raise ScenarioError(f"Scenario file not found: {p}") from e

    return loads(text, variables=variables, source=p)


def loads(
    text: str,
    *,
    variables: dict[str, str] | None = None,
    source: Path | None = None,
) -> Scenario:
    """Parse a scenario from a YAML string."""

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        location = f"{source}: " if source else ""
        raise ScenarioError(f"{location}YAML syntax error: {e}") from e

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ScenarioError("Scenario must be a mapping at the top level")

    if variables:
        raw = _substitute_vars(raw, variables)

    meta_raw = raw.get("meta") or {}
    steps_raw = raw.get("steps") or []
    if not isinstance(meta_raw, dict):
        raise ScenarioError("`meta` must be a mapping")
    if not isinstance(steps_raw, list):
        raise ScenarioError("`steps` must be a list")

    try:
        meta = Meta(**meta_raw)
    except ValidationError as e:
        raise ScenarioError(f"meta: {e}") from e

    canonical = [_normalize_step(s, i) for i, s in enumerate(steps_raw)]
    steps = _validate_steps(canonical, source)

    return Scenario(meta=meta, steps=steps)


# ------- Public: run ---------------------------------------------------------


def _session_kwargs_from_meta(meta: Meta) -> dict[str, Any]:
    return {
        "engine": meta.engine,
        "viewport": meta.viewport,
        "device": meta.device,
        "headful": meta.headful,
        "slowmo": meta.slowmo,
        "proxy": meta.proxy,
        "lang": meta.lang,
        "dark": meta.dark,
    }


async def run(
    scenario: Scenario,
    *,
    session: Session | None = None,
    recorder: Recorder | None = None,
) -> RunResult:
    """Execute a scenario end-to-end.

    If ``session`` is None, a fresh Session is built from ``scenario.meta``
    and torn down when we're done. Otherwise the caller's session is reused
    unchanged.
    """
    if session is not None:
        return await _run_with(scenario, session, recorder)

    async with Session(**_session_kwargs_from_meta(scenario.meta)) as sess:
        return await _run_with(scenario, sess, recorder)


async def _run_with(
    scenario: Scenario,
    session: Session,
    recorder: Recorder | None,
) -> RunResult:
    results: list[ActionResult] = []
    for i, step in enumerate(scenario.steps):
        # `repeat` is honored at the caller layer — see #4's PR notes
        for _ in range(step.repeat):
            if recorder is not None:
                await recorder.pre_action(session)
            result = await execute(step, session)
            if recorder is not None:
                await recorder.post_action(session, result, step)
            results.append(result)
            if not result.ok:
                # optional=True was already absorbed by execute() into
                # status="skipped" with ok=True, so anything not-ok here is
                # a genuine failure that should stop the run.
                return RunResult(results=results, failed_at=i)
    return RunResult(results=results, failed_at=None)
