"""CI-stable distillation of a sidecar :class:`Report` into an assertion set.

The raw sidecar carries wall-clock timestamps, recorded frame filenames,
per-step wall-clock durations, and fully-resolved URLs (including query
strings). All four change between runs even when the UI under test is
identical, so diffing two raw sidecars for a CI regression gate produces
false positives on every run.

This module distills the sidecar down to the shape that actually matters
for regression gating:

- ``step_count`` — did the scenario execute the same number of steps?
- ``steps[i]`` — for each step: action verb, human label, status, and the
  per-step counters (console errors, page errors, network failures) that
  the collector already accumulates.

Explicitly excluded: ``started_at``, ``duration_s``, per-step
``duration_ms``, ``frames`` (filenames are byte-identical only if the
recorder timing is identical), resolved URLs, ``cursor_xy``. See
:func:`build_assertions` for the full list of scrubbed keys.

The distilled shape is the CONTRACT — freeze it with a pydantic
``extra="forbid"`` model (:class:`~clickcast.feedback.models.Assertions`)
so a bug that quietly adds a new key can't slip through unnoticed.

Consumed by :meth:`clickcast.reel.Reel.assertions` /
:meth:`~clickcast.reel.Reel.assertions_diff` and by the
``clickcast assertions`` CLI subcommand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clickcast.feedback.models import Assertions, Report

__all__ = [
    "ASSERTIONS_SCHEMA_VERSION",
    "build_assertions",
    "diff_assertions",
    "load_assertions",
]

ASSERTIONS_SCHEMA_VERSION = 1


def build_assertions(report: Report) -> dict[str, Any]:
    """Return the deterministic, CI-stable distillation of ``report``.

    Byte-identical output for two runs of the same scenario against the same
    URL (modulo real behavioral drift in the target UI). Validated through
    :class:`~clickcast.feedback.models.Assertions` before it leaves this
    function so a shape regression fails fast at the boundary.
    """
    steps: list[dict[str, Any]] = []
    for s in report.steps:
        page_state = s.page_state
        steps.append(
            {
                "action": s.action,
                "label": s.label,
                "status": s.status,
                "console_error_count": (
                    len(page_state.console_errors) if page_state is not None else 0
                ),
                "page_error_count": (len(page_state.page_errors) if page_state is not None else 0),
                "network_failed_count": (
                    len(page_state.network_failed) if page_state is not None else 0
                ),
                # See #151 (AI-2, AI-5): CI baselines can pin the KIND of
                # skip / failure, not just the ``status`` verb — a step
                # going from ``skipped(optional_no_reaction)`` to
                # ``skipped(element_vanished)`` is real behavioural drift.
                "skip_reason": s.skip_reason,
                "error_code": s.error_code,
            }
        )
    payload: dict[str, Any] = {
        "schema_version": ASSERTIONS_SCHEMA_VERSION,
        "step_count": len(report.steps),
        "steps": steps,
    }
    # Round-trip through the pydantic model so a shape regression here fails
    # loudly before it lands on disk / in a CI diff.
    return Assertions.model_validate(payload).model_dump(mode="json")


def diff_assertions(current: dict[str, Any], baseline: dict[str, Any]) -> tuple[list[str], bool]:
    """Compare two assertion sets. Return ``(drift_descriptions, is_clean)``.

    ``drift_descriptions`` is a list of human-readable one-liners
    (``"step 2: status changed ok -> failed"``); ``is_clean`` is ``True``
    exactly when the list is empty. The order of the list is deterministic:
    top-level changes first (``schema_version``, ``step_count``), then per-
    step drift walked in index order, then per-key drift within each step
    walked in a fixed field order.

    Both inputs are treated as freshly-loaded JSON dicts. The function does
    NOT re-validate — a caller that wants strict shape enforcement should
    pass values that already round-tripped through :func:`build_assertions`
    (or through :class:`~clickcast.feedback.models.Assertions`).
    """
    drift: list[str] = []

    cur_version = current.get("schema_version")
    base_version = baseline.get("schema_version")
    if cur_version != base_version:
        drift.append(f"schema_version changed {base_version} -> {cur_version}")

    cur_count = current.get("step_count", 0)
    base_count = baseline.get("step_count", 0)
    if cur_count != base_count:
        drift.append(f"step_count changed {base_count} -> {cur_count}")

    cur_steps = current.get("steps") or []
    base_steps = baseline.get("steps") or []

    # Compare position-by-position up to min length; report per-index adds /
    # removes separately so a mid-scenario insertion isn't reported as N
    # cascading changes.
    common = min(len(cur_steps), len(base_steps))
    for i in range(common):
        drift.extend(_step_drift(i, cur_steps[i], base_steps[i]))

    for i in range(common, len(cur_steps)):
        drift.append(f"step {i}: added (action={cur_steps[i].get('action')!r})")
    for i in range(common, len(base_steps)):
        drift.append(f"step {i}: removed (was action={base_steps[i].get('action')!r})")

    return drift, not drift


def load_assertions(path: str | Path) -> dict[str, Any]:
    """Load an assertion set from JSON on disk.

    Convenience for CI recipes and the ``clickcast assertions`` command.
    Returns a plain dict — the caller is free to feed it straight to
    :func:`diff_assertions` or to hand it to
    :class:`~clickcast.feedback.models.Assertions` for strict validation.
    """
    text = Path(path).read_text()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"assertions file {path} did not deserialize to an object")
    return parsed


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------


# Walked in a fixed order so the drift-description list is deterministic
# for identical inputs — CI diffs shouldn't reshuffle across runs.
_STEP_FIELDS: tuple[str, ...] = (
    "action",
    "label",
    "status",
    "console_error_count",
    "page_error_count",
    "network_failed_count",
    "skip_reason",
    "error_code",
)


def _step_drift(index: int, current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for field in _STEP_FIELDS:
        cur = current.get(field)
        base = baseline.get(field)
        if cur != base:
            lines.append(f"step {index}: {field} changed {base!r} -> {cur!r}")
    return lines
