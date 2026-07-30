"""Long-form feedback session substrate — #124 v1.

Zero-network local capture of "what did I do with clickcast this week"
so a two-week friction report becomes a short command instead of a
two-hour writing session. See :mod:`clickcast.feedback.session.storage`
for the on-disk layout and :mod:`~.summary` for the rendering side.

v1 ships the substrate only: session lifecycle (start/stop/status/list),
one collector (per-invocation record), and a deterministic Markdown/JSON
summary. Heuristics engine (#124 Track 4) and the ``feedback file`` GH
emitter (#124 Track 6) are deferred — the substrate has to land first so
the pattern library iterates against real evidence.
"""

from __future__ import annotations

from clickcast.feedback.session.storage import (
    ActiveSession,
    InvocationEvent,
    SessionInfo,
    SessionStore,
    default_store,
    record_invocation_safe,
)
from clickcast.feedback.session.summary import (
    SessionSummary,
    render_json,
    render_markdown,
    summarize,
)

__all__ = [
    "ActiveSession",
    "InvocationEvent",
    "SessionInfo",
    "SessionStore",
    "SessionSummary",
    "default_store",
    "record_invocation_safe",
    "render_json",
    "render_markdown",
    "summarize",
]
