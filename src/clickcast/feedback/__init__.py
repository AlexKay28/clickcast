"""AI-feedback JSON sidecar — versioned schema + builder + public loader."""

from __future__ import annotations

import json
from pathlib import Path

from clickcast.feedback.builder import ReportBuilder
from clickcast.feedback.collector import PageStateCollector
from clickcast.feedback.models import (
    DiscoveredElement,
    Feedback,
    FeedbackTemplate,
    Media,
    PageState,
    Report,
    StepReport,
)
from clickcast.feedback.pointers import build_feedback, feedback_pointer_lines

__all__ = [
    "DiscoveredElement",
    "Feedback",
    "FeedbackTemplate",
    "Media",
    "PageState",
    "PageStateCollector",
    "Report",
    "ReportBuilder",
    "StepReport",
    "build_feedback",
    "feedback_pointer_lines",
    "load",
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
