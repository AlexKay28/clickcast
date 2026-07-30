"""Deterministic summary of a recorded session.

Reads a :class:`~.storage.SessionInfo` + its event stream and returns
the same bytes on every invocation given identical input. Determinism
matters for two reasons:

1. The v1 test suite pins byte-for-byte equality (so drift catches
   accidental non-determinism the moment someone reintroduces it).
2. The eventual heuristics engine (#124 Track 4) will diff summaries
   across sessions to spot "you keep hitting the same wall" patterns
   — that only works if the base summary is stable.

v1 renders four sections: total invocations, argv-pattern histogram
(top 3), failed-invocation list, and session duration. The
heuristics/pain-signal layer described in the #124 resolution plan
comes next — this file stays focused on *what happened* rather than
*what it means*.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from clickcast.feedback.session.storage import InvocationEvent, SessionInfo

__all__ = [
    "SessionSummary",
    "render_json",
    "render_markdown",
    "summarize",
]


_TOP_N_PATTERNS = 3


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Aggregate view of one session — the summary renderers' input.

    Frozen dataclass (not Pydantic) because it's a value type computed
    from the session on demand — never round-tripped to disk, never
    validated at a boundary. Sorted fields are already-canonicalized
    for the byte-deterministic renderers below.
    """

    session_id: str
    label: str | None
    started_at: str
    stopped_at: str | None
    duration_seconds: int | None
    invocation_count: int
    failed_count: int
    top_patterns: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    failed_invocations: tuple[tuple[str, int], ...] = field(default_factory=tuple)


def summarize(info: SessionInfo, events: list[InvocationEvent]) -> SessionSummary:
    """Compute the summary from raw session state + events.

    Pattern extraction collapses argv into "clickcast <subcommand> …"
    so cosmetically-different-but-semantically-identical invocations
    group together (e.g. two ``clickcast auto <url1>`` and
    ``clickcast auto <url2>`` runs both fall under
    ``clickcast auto``). Full argv is preserved on the raw event, so a
    later heuristics pass can look at the argument shape.
    """
    invocation_count = len(events)
    failed_events = [e for e in events if e.exit_code != 0]
    failed_count = len(failed_events)

    counter: Counter[str] = Counter(_argv_pattern(e.argv) for e in events)
    # Sort by count desc, then by pattern asc for byte-stable output.
    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N_PATTERNS]

    # Failed invocations: sorted by (pattern, count) for determinism.
    failed_counter: Counter[str] = Counter(_argv_pattern(e.argv) for e in failed_events)
    failed = sorted(failed_counter.items(), key=lambda kv: (kv[0], -kv[1]))

    duration = _duration_seconds(info)

    return SessionSummary(
        session_id=info.session_id,
        label=info.label,
        started_at=info.started_at,
        stopped_at=info.stopped_at,
        duration_seconds=duration,
        invocation_count=invocation_count,
        failed_count=failed_count,
        top_patterns=tuple(top),
        failed_invocations=tuple(failed),
    )


def render_markdown(summary: SessionSummary) -> str:
    """Markdown rendering — one screenful, deterministic, human-first.

    Kept intentionally spare: the v1 goal is "prove the substrate
    works end-to-end", not "produce a polished #105-style essay". The
    heuristics layer (#124 Track 4) will add the interpretive prose
    — this file just lays out the raw counts.
    """
    lines: list[str] = []
    header = f"# Feedback session {summary.session_id}"
    if summary.label:
        header += f" — {summary.label}"
    lines.append(header)
    lines.append("")
    lines.append("## Overview")
    lines.append(f"- Started: {summary.started_at}")
    lines.append(f"- Stopped: {summary.stopped_at or '(still active)'}")
    if summary.duration_seconds is not None:
        lines.append(f"- Duration: {_format_duration(summary.duration_seconds)}")
    lines.append(f"- Invocations recorded: {summary.invocation_count}")
    lines.append(f"- Failed invocations: {summary.failed_count}")
    lines.append("")

    lines.append("## Most-frequent commands")
    if summary.top_patterns:
        for pattern, count in summary.top_patterns:
            lines.append(f"- `{pattern}` — {count}")
    else:
        lines.append("- (no invocations recorded yet)")
    lines.append("")

    lines.append("## Failed invocations")
    if summary.failed_invocations:
        for pattern, count in summary.failed_invocations:
            lines.append(f"- `{pattern}` — {count} failure(s)")
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def render_json(summary: SessionSummary) -> str:
    """JSON rendering — sorted keys + indent=2 for byte-stable output."""
    payload: dict[str, Any] = {
        "session_id": summary.session_id,
        "label": summary.label,
        "started_at": summary.started_at,
        "stopped_at": summary.stopped_at,
        "duration_seconds": summary.duration_seconds,
        "invocation_count": summary.invocation_count,
        "failed_count": summary.failed_count,
        "top_patterns": [{"pattern": p, "count": c} for p, c in summary.top_patterns],
        "failed_invocations": [{"pattern": p, "count": c} for p, c in summary.failed_invocations],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _argv_pattern(argv: list[str]) -> str:
    """Collapse an argv list to a stable "shape" string.

    v1 policy: take the first positional token (typically the
    subcommand — e.g. ``auto``, ``run``, ``skill``) and render it as
    ``clickcast <subcommand>``. Everything else is intentionally
    dropped so the histogram groups by intent, not by target URL /
    output filename. Heuristics can operate on the raw argv on the
    event itself.
    """
    for token in argv:
        if not token.startswith("-"):
            return f"clickcast {token}"
    return "clickcast"


def _duration_seconds(info: SessionInfo) -> int | None:
    if info.stopped_at is None:
        return None
    try:
        start = _parse_iso(info.started_at)
        stop = _parse_iso(info.stopped_at)
    except ValueError:
        return None
    delta = stop - start
    seconds = int(delta.total_seconds())
    return max(0, seconds)


def _parse_iso(value: str) -> datetime:
    """Accept ``…Z`` (from the storage layer) as well as ``…+00:00``."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _format_duration(seconds: int) -> str:
    """Human duration ("2h 34m 12s"). No leading-zero padding — this
    string is user-facing, not another parser's input."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)
