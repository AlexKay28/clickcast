"""AI-feedback JSON sidecar — versioned schema + builder + public loader."""

from __future__ import annotations

import json
from pathlib import Path

from clickcast.feedback.assertions import (
    ASSERTIONS_SCHEMA_VERSION,
    build_assertions,
    diff_assertions,
    load_assertions,
)
from clickcast.feedback.builder import ReportBuilder
from clickcast.feedback.collector import PageStateCollector
from clickcast.feedback.models import (
    Assertions,
    DiscoveredElement,
    Feedback,
    FeedbackTemplate,
    Media,
    PageState,
    Report,
    StepAssertion,
    StepReport,
)
from clickcast.feedback.pointers import build_feedback, feedback_pointer_lines

__all__ = [
    "ASSERTIONS_SCHEMA_VERSION",
    "Assertions",
    "DiscoveredElement",
    "Feedback",
    "FeedbackTemplate",
    "Media",
    "PageState",
    "PageStateCollector",
    "Report",
    "ReportBuilder",
    "StepAssertion",
    "StepReport",
    "build_assertions",
    "build_feedback",
    "diff_assertions",
    "feedback_pointer_lines",
    "load",
    "load_assertions",
    "write",
]


def load(path: str | Path) -> Report:
    """Load a sidecar JSON from disk and validate it against the current schema."""
    text = Path(path).read_text()
    return Report.model_validate_json(text)


def write(report: Report, path: str | Path, *, with_feedback: bool = False) -> Path:
    """Serialize ``report`` to disk as pretty-printed JSON.

    When ``with_feedback`` is set, attach a :class:`Feedback` pointer block
    (repo URL, issues URL, prefilled new-issue URL, prompt template) so
    downstream AI-agent consumers of the sidecar can file bug reports and
    ideas without hunting for the repo. See :func:`clickcast.feedback.pointers.build_feedback`.
    """
    if with_feedback and report.feedback is None:
        report = report.model_copy(update={"feedback": build_feedback(report)})
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.model_dump(mode="json"), indent=2))
    return out
