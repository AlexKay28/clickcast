"""AI-feedback JSON sidecar — versioned schema + builder + public loader."""

from __future__ import annotations

import json
import re
from pathlib import Path

from clickcast.feedback.advisories import Advisory, build_advisories
from clickcast.feedback.assertions import (
    ASSERTIONS_SCHEMA_VERSION,
    build_assertions,
    diff_assertions,
    load_assertions,
)
from clickcast.feedback.builder import ReportBuilder
from clickcast.feedback.collector import PageStateCollector
from clickcast.feedback.graph import build_graph, dom_signature
from clickcast.feedback.models import (
    AnnotateMetadata,
    Assertions,
    BBox,
    ComponentNode,
    DiscoveredElement,
    Edge,
    Feedback,
    FeedbackTemplate,
    Graph,
    GridMetadata,
    Media,
    PageNode,
    PageState,
    Report,
    StepAssertion,
    StepReport,
    StepVisualDiff,
    UnmatchedStep,
    VisualDiffReport,
)
from clickcast.feedback.pointers import build_feedback, feedback_pointer_lines
from clickcast.feedback.redact import apply_patterns
from clickcast.feedback.redact import strip_query_strings as _strip_query_strings
from clickcast.feedback.visual_diff import VISUAL_DIFF_SCHEMA_VERSION, visual_diff

__all__ = [
    "ASSERTIONS_SCHEMA_VERSION",
    "VISUAL_DIFF_SCHEMA_VERSION",
    "Advisory",
    "AnnotateMetadata",
    "Assertions",
    "BBox",
    "ComponentNode",
    "DiscoveredElement",
    "Edge",
    "Feedback",
    "FeedbackTemplate",
    "Graph",
    "GridMetadata",
    "Media",
    "PageNode",
    "PageState",
    "PageStateCollector",
    "Report",
    "ReportBuilder",
    "StepAssertion",
    "StepReport",
    "StepVisualDiff",
    "UnmatchedStep",
    "VisualDiffReport",
    "build_advisories",
    "build_assertions",
    "build_feedback",
    "build_graph",
    "diff_assertions",
    "dom_signature",
    "feedback_pointer_lines",
    "load",
    "load_assertions",
    "visual_diff",
    "write",
]


def load(path: str | Path) -> Report:
    """Load a sidecar JSON from disk and validate it against the current schema."""
    text = Path(path).read_text()
    return Report.model_validate_json(text)


def write(
    report: Report,
    path: str | Path,
    *,
    with_feedback: bool = False,
    redact_patterns: list[re.Pattern[str]] | None = None,
    strip_query_strings: bool = False,
) -> Path:
    """Serialize ``report`` to disk as pretty-printed JSON.

    When ``with_feedback`` is set, attach a :class:`Feedback` pointer block
    (repo URL, issues URL, prefilled new-issue URL, prompt template) so
    downstream AI-agent consumers of the sidecar can file bug reports and
    ideas without hunting for the repo. See :func:`clickcast.feedback.pointers.build_feedback`.

    ``redact_patterns`` and ``strip_query_strings`` are the token-leak
    footgun fix (#110). Sidecars from auth-bypassed previews (Vercel /
    Cloudflare / Netlify) leak the bypass token in every recorded URL —
    supply patterns to blot out the token(s), and/or drop query strings
    entirely. Applied on a copy of the report BEFORE the feedback block
    is built: :func:`build_feedback` embeds the target URL in the prefilled
    new-issue URL, so we have to scrub the report first or the token would
    reappear URL-encoded inside the pointer block. The static repo/schema
    /docs pointers themselves are constants and are added AFTER redaction
    — they are never rewritten.
    """
    redacted = _redact_report(
        report,
        redact_patterns=redact_patterns,
        should_strip_query_strings=strip_query_strings,
    )
    if with_feedback and redacted.feedback is None:
        redacted = redacted.model_copy(update={"feedback": build_feedback(redacted)})
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(redacted.model_dump(mode="json"), indent=2))
    return out


def _redact_report(
    report: Report,
    *,
    redact_patterns: list[re.Pattern[str]] | None,
    should_strip_query_strings: bool,
) -> Report:
    """Return a copy of ``report`` with the #110 redactions applied.

    Both passes operate on the dumped-to-dict form and the result is
    re-validated back into a :class:`Report` so downstream consumers
    (including :func:`build_feedback`) see the cleaned URLs. When neither
    knob is set this is a no-op fast path — the original report passes
    through untouched.

    Order matters: strip query strings FIRST so patterns targeting a value
    that appears exclusively in a query string (``token=xyz``) still redact
    any residual copies that live outside URL fields (log lines, error
    messages, screenshot filenames). ``apply_patterns`` walks every string
    in the tree; ``strip_query_strings`` only touches URL-shaped fields.
    """
    if not redact_patterns and not should_strip_query_strings:
        return report
    payload = report.model_dump(mode="json")
    if should_strip_query_strings:
        payload = _strip_query_strings(payload)
    if redact_patterns:
        payload = apply_patterns(payload, redact_patterns)
    return Report.model_validate(payload)
