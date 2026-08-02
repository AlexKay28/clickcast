"""YAML scenario parser + runner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from clickcast.core.actions import ActionResult, Step, execute
from clickcast.core.opts import BrowserOpts, RenderOpts
from clickcast.core.session import Session
from clickcast.core.viewport import Viewport

if TYPE_CHECKING:
    from clickcast.capture import Recorder
    from clickcast.feedback import ReportBuilder


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


_BROWSER_FIELDS: frozenset[str] = frozenset(
    {"engine", "viewport", "device", "headful", "lang", "dark", "slowmo", "proxy"}
)
_RENDER_FIELDS: frozenset[str] = frozenset({"fps", "quality", "loop", "format"})


class Meta(BaseModel):
    """Scenario-level meta configuration.

    Since #97 the browser-behaviour and render-output fields are grouped
    into nested :class:`~clickcast.core.opts.BrowserOpts` and
    :class:`~clickcast.core.opts.RenderOpts`. Existing YAML scenarios that
    used flat fields (``engine: chromium`` at ``meta`` root) still load —
    a :func:`~pydantic.model_validator` migrates the flat shape into
    ``browser`` / ``render`` before pydantic validates. Old flat readers
    (``meta.engine``, ``meta.viewport``, ...) are preserved as
    ``@property`` accessors so existing call sites don't have to change
    (writers use the nested shape: ``meta.browser.headful = True``).
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    name: str | None = None
    browser: BrowserOpts = Field(default_factory=BrowserOpts)
    render: RenderOpts = Field(default_factory=RenderOpts)
    dwell: float = 1.0
    out: str = "reel.gif"
    # Free-form until #8 defines AnnotateConfig
    annotate: dict[str, Any] | None = None
    # Optional include-parent path — deferred implementation per roadmap
    extends: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_flat_to_nested(cls, values: Any) -> Any:
        """Accept legacy flat YAML (``engine: chromium`` at ``meta`` root)
        by moving those keys into a synthesized ``browser`` / ``render``
        sub-dict before pydantic validation. New scenarios can write
        either shape (or a mix — the flat keys always win over any
        nested ``browser: {}``, which is the natural user expectation)."""
        if not isinstance(values, dict):
            return values
        values = dict(values)
        browser = dict(values.pop("browser", None) or {})
        render = dict(values.pop("render", None) or {})
        for key in list(values):
            if key in _BROWSER_FIELDS:
                # Flat wins over any nested value the user also typed —
                # matches the natural "more specific / later" override
                # intuition.
                browser[key] = values.pop(key)
            elif key in _RENDER_FIELDS:
                render[key] = values.pop(key)
        # `viewport` and BrowserOpts want a `Viewport`, but flat YAML sends
        # a string ("1280x800"). Coerce here so pydantic doesn't complain
        # about the arbitrary type.
        if "viewport" in browser and browser["viewport"] is not None:
            browser["viewport"] = Viewport.parse(browser["viewport"])
        if browser:
            values["browser"] = BrowserOpts(**browser)
        if render:
            values["render"] = RenderOpts(**render)
        return values

    # --- Backwards-compat flat readers ---------------------------------
    # Keep the shipped `meta.engine`, `meta.viewport`, ... call sites
    # working without a codebase-wide rename. Writers migrate to the
    # nested shape (`meta.browser.headful = True`).

    @property
    def engine(self) -> str:
        return self.browser.engine

    @property
    def viewport(self) -> str | None:
        return str(self.browser.viewport) if self.browser.viewport else None

    @property
    def device(self) -> str | None:
        return self.browser.device

    @property
    def headful(self) -> bool:
        return self.browser.headful

    @property
    def lang(self) -> str | None:
        return self.browser.lang

    @property
    def dark(self) -> bool:
        return self.browser.dark

    @property
    def slowmo(self) -> int:
        return self.browser.slowmo

    @property
    def proxy(self) -> str | None:
        return self.browser.proxy

    @property
    def fps(self) -> int:
        return self.render.fps

    @property
    def quality(self) -> int:
        return self.render.quality

    @property
    def loop(self) -> int:
        return self.render.loop

    @property
    def format(self) -> str:
        return self.render.format


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
    "select", "scroll", "wait", "wait_for", "screenshot", "evaluate", "wheel",
}  # fmt: skip

_COMMON_KEYS = {"label", "dwell", "optional", "repeat"}

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _substitute_vars(obj: Any, variables: dict[str, str]) -> Any:
    """Recursively replace `{{ name }}` placeholders. Raises on undefined names."""

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            # See #151 (AI-6): the old message ("undefined variable {{ foo }}")
            # leaked template-syntax jargon and gave no fix hint. Spell out
            # both remediation paths so an AI agent hitting this can self-
            # repair without reading the source.
            raise ScenarioError(
                f"undefined scenario variable '{name}' — pass "
                f"'--var {name}=<value>' on the CLI or declare it under "
                f"'variables:' in the scenario YAML"
            )
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

    elif action == "wait_for":
        if isinstance(value, str):
            canonical["selector"] = value
        elif isinstance(value, dict):
            canonical.update(value)
        else:
            raise ScenarioError(
                f"step {index}: wait_for value must be a selector string or mapping"
            )

    elif action == "screenshot":
        if isinstance(value, dict):
            canonical.update(value)
        elif not isinstance(value, bool | int | str | type(None)):
            raise ScenarioError(f"step {index}: screenshot value must be a mapping or scalar")
        # bare `screenshot:` (with no options) is fine — nothing to merge

    elif action == "evaluate":
        if isinstance(value, str):
            canonical["expression"] = value
        elif isinstance(value, dict):
            canonical.update(value)
        else:
            raise ScenarioError(
                f"step {index}: evaluate value must be a JS expression string or mapping"
            )

    elif action == "wheel":
        if isinstance(value, int):
            # Bare `wheel: 120` — vertical delta only.
            canonical["dy"] = value
        elif isinstance(value, dict):
            canonical.update(value)
        else:
            raise ScenarioError(f"step {index}: wheel value must be an int (dy) or mapping")

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
    builder: ReportBuilder | None = None,
) -> RunResult:
    """Execute a scenario end-to-end.

    If ``session`` is None, a fresh Session is built from ``scenario.meta``
    and torn down when we're done. Otherwise the caller's session is reused
    unchanged.

    An optional ``builder`` (from :mod:`clickcast.feedback`) receives per-step
    reports; caller finalizes it after encoding. The builder is attached to
    the session's page here, so it must be passed **before** any step runs.
    """
    if session is not None:
        return await _run_with(scenario, session, recorder, builder)

    async with Session(**_session_kwargs_from_meta(scenario.meta)) as sess:
        return await _run_with(scenario, sess, recorder, builder)


async def _run_with(
    scenario: Scenario,
    session: Session,
    recorder: Recorder | None,
    builder: ReportBuilder | None,
) -> RunResult:
    if builder is not None:
        builder.attach(session)

    results: list[ActionResult] = []
    for i, step in enumerate(scenario.steps):
        # `repeat` is honored at the caller layer — see #4's PR notes
        for _ in range(step.repeat):
            frames_this_step: list[Any] = []
            if recorder is not None:
                await recorder.pre_action(session)
            result = await execute(step, session, step_index=i)
            if recorder is not None:
                frames_this_step = await recorder.post_action(session, result, step)
            results.append(result)

            if builder is not None:
                await builder.record_step(
                    index=i,
                    step=step,
                    result=result,
                    frames=frames_this_step,
                )

            if not result.ok:
                # optional=True was already absorbed by execute() into
                # status="skipped" with ok=True, so anything not-ok here is
                # a genuine failure that should stop the run.
                return RunResult(results=results, failed_at=i)
    return RunResult(results=results, failed_at=None)
