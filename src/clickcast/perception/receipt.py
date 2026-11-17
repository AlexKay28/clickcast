"""``receipt()`` — the closing call of the perception loop (#271).

The loop is ``observe → act → settle → receipt``. The first three primitives
answer *what can I act on*, *what happened*, and *has the page stopped
changing*; ``receipt`` answers the only question left at the end of a run:

    "Did it work, what is the one thing that broke, and where are the details?"

It therefore returns a **summary**, not a transcript. Everything bulky — the
per-step records, console/network counters, frame paths, the graph block —
stays in the sidecar the summary points at. The summary itself is bounded:

* :data:`SCHEMA` is ``"receipt/1"``;
* :data:`RECEIPT_TOKEN_BUDGET` is 200 estimated tokens, measured with
  :func:`clickcast.perception.tokens.estimate_tokens` on the *exact* compact
  JSON that is returned (the same estimator/serializer pair ``observe()``
  uses, so a consumer can reason about both payloads with one rule);
* blocks with nothing to report are omitted — ``assertions`` only when a
  baseline was diffed, ``visual_diff`` only when a baseline sidecar was
  compared, ``top_error`` only when a step actually failed.

Artifact layout
===============
Every artifact path in the summary is **relative to one directory** (the
``out_dir`` passed to :func:`write_receipt`):

::

    tour.mp4            # only when media was requested (Reel/MCP)
    tour.mp4.json       # sidecar, schema v5 — the detail lives here
    assertions.json     # CI-stable distillation of the sidecar
    baseline.json       # copy of the baseline the drift was measured against
    visual-diff/        # region-highlighted diff PNGs + summary.json
    receipt.json        # this summary

The module is deliberately pure with respect to media: it never encodes and
never imports :mod:`clickcast.encode`. Callers run the render kernel
(:func:`clickcast.kernel.render_reel`) and the shared sidecar writer
(:func:`clickcast.kernel.sidecar.write_sidecar`) first, then hand the paths
here to be summarized.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from clickcast.feedback import Report, StepReport
from clickcast.feedback.assertions import build_assertions, diff_assertions, load_assertions
from clickcast.feedback.models import VisualDiffReport
from clickcast.perception.tokens import compact_json, estimate_tokens

__all__ = [
    "ASSERTIONS_FILENAME",
    "RECEIPT_FILENAME",
    "RECEIPT_TOKEN_BUDGET",
    "SCHEMA",
    "build_receipt",
    "write_receipt",
]

#: Schema tag for the summary payload.
SCHEMA = "receipt/1"

#: Hard ceiling on the summary, in estimated tokens (see ``perception/tokens``).
RECEIPT_TOKEN_BUDGET = 200

#: Name of the summary file written into the artifact directory.
RECEIPT_FILENAME = "receipt.json"

#: Name of the assertion distillation written into the artifact directory.
ASSERTIONS_FILENAME = "assertions.json"


# --------------------------------------------------------------------------
# Pure payload assembly
# --------------------------------------------------------------------------


def _relative_to(path: str | Path, out_dir: str | Path) -> str:
    """Return ``path`` relative to ``out_dir`` (basename when outside it)."""
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(Path(out_dir).resolve()).as_posix()
    except ValueError:
        # The caller pointed at a file outside the artifact directory: the
        # summary must not smuggle an absolute path into a portable artifact.
        return candidate.name


def _step_counts(steps: Iterable[StepReport]) -> dict[str, int]:
    counts = {"ok": 0, "failed": 0, "skipped": 0}
    for step in steps:
        if step.status in counts:
            counts[step.status] += 1
    return counts


def _top_error(steps: Iterable[StepReport]) -> dict[str, Any] | None:
    """The first step that actually failed, as ``{step, error_code}``.

    ``step`` is the **0-based position** in ``report.steps`` (the same
    ``StepReport.index`` a batch run records). ``error_code`` is that step's
    enumerated code — never the human-readable ``error`` string, which is
    what agents used to have to regex.
    """
    for position, step in enumerate(steps):
        if step.status == "failed":
            return {"step": position, "error_code": step.error_code}
    return None


def _visual_diff_block(
    visual_diff: VisualDiffReport | Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Collapse a full :class:`VisualDiffReport` to two summary numbers."""
    if visual_diff is None:
        return None
    if isinstance(visual_diff, VisualDiffReport):
        steps = [step.model_dump(mode="json") for step in visual_diff.steps]
    else:
        steps = list(visual_diff.get("steps") or [])
    changed = [
        int(step["run_index"]) for step in steps if float(step.get("changed_pct") or 0.0) > 0.0
    ]
    max_pct = max((float(step.get("changed_pct") or 0.0) for step in steps), default=0.0)
    return {"changed_steps": changed, "max_changed_pct": round(max_pct, 1)}


def build_receipt(
    report: Report,
    *,
    out_dir: str | Path,
    media_path: str | Path | None = None,
    sidecar_path: str | Path | None = None,
    drift: int | None = None,
    baseline: str | Path | None = None,
    visual_diff: VisualDiffReport | Mapping[str, Any] | None = None,
    token_budget: int = RECEIPT_TOKEN_BUDGET,
) -> dict[str, Any]:
    """Build the ``receipt/1`` summary for ``report`` (no filesystem writes).

    ``media_path`` / ``sidecar_path`` are the artifacts that were already
    written into ``out_dir``; their paths in the summary are relative to it.
    ``drift`` is the number of assertion-drift lines measured against
    ``baseline`` (see :func:`clickcast.feedback.assertions.diff_assertions`).

    The returned dict always satisfies
    ``estimate_tokens(compact_json(summary)) == summary["tokens"]["estimated"]``
    and that value is ``<= token_budget``: optional blocks are dropped in a
    fixed order (``visual_diff``, then ``assertions``) until it fits, and the
    budget is enforced here rather than trusted from callers.
    """
    payload: dict[str, Any] = {"schema": SCHEMA}
    if media_path is not None:
        payload["media"] = {
            "path": _relative_to(media_path, out_dir),
            "duration_s": round(report.media.duration_s, 1),
            "frames": report.media.frame_count,
        }
    if sidecar_path is not None:
        payload["sidecar"] = _relative_to(sidecar_path, out_dir)
        payload["sidecar_version"] = report.schema_version
    payload["steps"] = _step_counts(report.steps)

    top_error = _top_error(report.steps)
    if top_error is not None:
        payload["top_error"] = top_error

    if drift is not None:
        block: dict[str, Any] = {"drift": int(drift)}
        if baseline is not None:
            block["baseline"] = _relative_to(baseline, out_dir)
        payload["assertions"] = block

    visual = _visual_diff_block(visual_diff)
    if visual is not None:
        payload["visual_diff"] = visual

    return _enforce_budget(payload, token_budget=token_budget)


def _with_token_block(payload: dict[str, Any], *, token_budget: int) -> dict[str, Any]:
    """Stamp ``tokens`` at its fixed point: the estimate of the stamped payload.

    ``estimated`` counts its own digits, so it is computed by iterating until
    the measured value equals the value written — exactly what a consumer
    recomputing ``estimate_tokens(compact_json(payload))`` will get.
    """
    stamped = dict(payload)
    estimated = 0
    while True:
        stamped["tokens"] = {"budget": token_budget, "estimated": estimated}
        measured = estimate_tokens(compact_json(stamped))
        if measured == estimated:
            return stamped
        estimated = measured


def _enforce_budget(payload: dict[str, Any], *, token_budget: int) -> dict[str, Any]:
    """Drop optional blocks until the summary fits, then shorten paths.

    The fixed order keeps the summary deterministic: a caller who asked for a
    visual diff gets it whenever it fits, and the cheap-to-lose blocks go
    before any core field does.
    """
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")
    current = dict(payload)
    while True:
        stamped = _with_token_block(current, token_budget=token_budget)
        if stamped["tokens"]["estimated"] <= token_budget:
            return stamped
        for optional in ("visual_diff", "assertions"):
            if optional in current:
                current = {k: v for k, v in current.items() if k != optional}
                break
        else:
            # Only core blocks are left: shrink the two variable-length
            # strings (artifact names) to their basenames, then give up.
            media = current.get("media")
            sidecar = current.get("sidecar")
            shrinkable = (isinstance(media, dict) and "/" in str(media.get("path", ""))) or (
                isinstance(sidecar, str) and "/" in sidecar
            )
            if not shrinkable:
                raise RuntimeError(
                    f"receipt/1 cannot fit {token_budget} estimated tokens "
                    f"({stamped['tokens']['estimated']}): shorten the artifact names"
                )
            if isinstance(media, dict):
                media["path"] = Path(str(media["path"])).name
            if isinstance(sidecar, str):
                current["sidecar"] = Path(sidecar).name


# --------------------------------------------------------------------------
# Artifact-directory writer
# --------------------------------------------------------------------------


def write_receipt(
    report: Report,
    out_dir: str | Path,
    *,
    media_path: str | Path | None = None,
    sidecar_path: str | Path | None = None,
    baseline: str | Path | None = None,
    visual_diff: VisualDiffReport | Mapping[str, Any] | None = None,
    token_budget: int = RECEIPT_TOKEN_BUDGET,
) -> dict[str, Any]:
    """Write the artifact set into ``out_dir`` and return the summary.

    The sidecar (and, when media was requested, the media file) must already
    exist — this function only adds the derived artifacts it owns:
    ``assertions.json`` (always), a copy of ``baseline`` (when given),
    ``visual_diff.json`` (when given) and ``receipt.json``. Artifact paths in
    the returned summary are relative to ``out_dir``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    assertions_doc = build_assertions(report)
    (out / ASSERTIONS_FILENAME).write_text(
        json.dumps(assertions_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    drift: list[str] | None = None
    baseline_copy: Path | None = None
    if baseline is not None:
        source = Path(baseline)
        baseline_doc = load_assertions(source)
        drift, _clean = diff_assertions(assertions_doc, baseline_doc)
        # Keep the artifact directory self-contained and every summary path
        # loadable: copy a baseline that lives outside the directory in.
        target = out / source.name
        if source.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        baseline_copy = target

    if visual_diff is not None:
        payload = (
            visual_diff.model_dump(mode="json")
            if isinstance(visual_diff, VisualDiffReport)
            else dict(visual_diff)
        )
        (out / "visual_diff.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    summary = build_receipt(
        report,
        out_dir=out,
        media_path=media_path,
        sidecar_path=sidecar_path,
        drift=None if drift is None else len(drift),
        baseline=baseline_copy,
        visual_diff=visual_diff,
        token_budget=token_budget,
    )
    (out / RECEIPT_FILENAME).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary
