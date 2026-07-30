"""On-disk session storage — JSONL events + a tiny per-session TOML.

Design notes
------------
- **Path layout.** ``<state_root>/clickcast/feedback/`` holds one
  subdirectory per session (name = the session id). Each subdirectory
  contains ``session.toml`` (session lifecycle state — id, label,
  started_at, stopped_at) and ``events.jsonl`` (one JSON object per
  line, one line per recorded event). The active-session pointer lives
  in ``<root>/active.toml`` with a single ``session_id`` field.
  ``state_root`` respects ``$XDG_STATE_HOME`` on Linux and falls back
  to ``~/.local/state`` — matches the XDG Base Directory Specification
  and keeps the noisy per-invocation state out of ``~/.config`` (which
  holds user *config*, not state). The rest of clickcast already uses
  ``platformdirs.user_config_dir`` for ``config.toml``; session state
  is a different lifecycle (mutable, high-churn) so a different
  directory is right.
- **Append-only JSONL.** Every event append is one ``open("a")`` +
  ``write`` + ``close`` so concurrent CLI invocations don't step on
  each other in a fatal way. Full flock is overkill for a
  single-developer local tool; the last-writer-wins semantics of
  ``O_APPEND`` on POSIX is good enough (each line is written whole).
- **Zero-crash promise.** :func:`record_invocation_safe` swallows every
  exception. A broken session file must NEVER make the wrapped
  clickcast command fail — the whole point of the feature is that
  users stop thinking about it after ``feedback start``.

The public model classes here are Pydantic v2 with ``extra="forbid"``
so a malformed session.toml surfaces at load time rather than silently
losing fields.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — CI covers 3.11+
    import tomli as tomllib

import tomlkit
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ActiveSession",
    "InvocationEvent",
    "SessionInfo",
    "SessionStore",
    "default_store",
    "record_invocation_safe",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SessionInfo(BaseModel):
    """The ``session.toml`` shape — one per recorded session.

    ``extra="forbid"`` on purpose: a typo (``lable=...``) should fail loud
    at load time rather than get silently dropped. Fields chosen for what
    the v1 summary renderer needs — id (path-safe), an optional
    human-facing label, and start/stop timestamps for duration math.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    label: str | None = None
    started_at: str  # ISO-8601 UTC
    stopped_at: str | None = None


class InvocationEvent(BaseModel):
    """One line in ``events.jsonl`` — a single clickcast CLI invocation.

    ``kind`` is a discriminator field: v1 only emits ``"invocation"``,
    but the JSONL format is future-proof for the sidecar/error/reel-file
    collectors listed in the #124 resolution plan. ``argv`` deliberately
    drops ``sys.argv[0]`` so paths to the clickcast script don't leak
    (also makes summary grouping stable across ``clickcast`` vs
    ``python -m clickcast`` invocations).
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = "invocation"
    ts: str  # ISO-8601 UTC
    argv: list[str] = Field(default_factory=list)
    exit_code: int = 0
    wall_time_ms: int = Field(ge=0, default=0)
    cwd: str = ""
    git_rev: str | None = None


@dataclass(frozen=True, slots=True)
class ActiveSession:
    """Snapshot of the pointer file — which session is currently recording."""

    session_id: str
    label: str | None
    started_at: str


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _default_state_root() -> Path:
    """XDG-compliant state directory for clickcast.

    Resolution order:

    1. ``$CLICKCAST_FEEDBACK_ROOT`` — an explicit escape hatch, mainly
       for tests and one-off ``env CLICKCAST_FEEDBACK_ROOT=/tmp/foo …``
       invocations. Value is used verbatim (no ``clickcast/feedback``
       suffix appended) so tests can point at a self-contained tmp
       dir without one level of surprise nesting.
    2. ``$XDG_STATE_HOME`` — the XDG-Base-Directory spec's variable
       for high-churn state, e.g. ``$XDG_STATE_HOME/clickcast/feedback``.
    3. ``~/.local/state/clickcast/feedback`` — the XDG-recommended
       fallback when ``$XDG_STATE_HOME`` is unset.

    ``~/.config/clickcast`` (used by :mod:`clickcast.config` for
    ``config.toml``) is deliberately NOT reused — that dir holds
    stable user *configuration*; sessions are mutable, high-write
    *state*, which belongs elsewhere per XDG.
    """
    override = os.environ.get("CLICKCAST_FEEDBACK_ROOT")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_STATE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return root / "clickcast" / "feedback"


class SessionStore:
    """Filesystem-backed session store — one directory per session.

    The store is stateless (all state is on disk); the object just carries
    the root path so callers can point at a temp dir in tests. Every
    write is atomic-ish (``mkdir(parents=True, exist_ok=True)`` + a
    single ``write_text`` for TOML, ``open("a")`` for JSONL).
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else _default_state_root()

    # ---- paths --------------------------------------------------------

    @property
    def active_pointer_path(self) -> Path:
        return self.root / "active.toml"

    def session_dir(self, session_id: str) -> Path:
        return self.root / session_id

    def session_info_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.toml"

    def events_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "events.jsonl"

    # ---- lifecycle ----------------------------------------------------

    def start(self, label: str | None = None) -> SessionInfo:
        """Begin a new session and mark it active.

        A second ``start`` while a session is already active is a no-op
        semantically for callers — we return the currently-active session
        rather than clobbering it. Callers who want a hard error can
        check :meth:`active` first.
        """
        current = self.active()
        if current is not None:
            info = self.load_info(current.session_id)
            if info is not None:
                return info
        session_id = _new_session_id()
        info = SessionInfo(
            session_id=session_id,
            label=label,
            started_at=_now_iso(),
            stopped_at=None,
        )
        self._write_info(info)
        self._write_active(info)
        return info

    def stop(self) -> SessionInfo | None:
        """End the active session and clear the pointer.

        Returns the stopped session's :class:`SessionInfo` (with
        ``stopped_at`` populated), or ``None`` if no session was active.
        Idempotent — calling ``stop`` twice returns ``None`` the second
        time rather than raising.
        """
        current = self.active()
        if current is None:
            return None
        info = self.load_info(current.session_id)
        if info is None:
            # Pointer references a session whose dir was deleted; just
            # clear the pointer and move on.
            self._clear_active()
            return None
        info = info.model_copy(update={"stopped_at": _now_iso()})
        self._write_info(info)
        self._clear_active()
        return info

    def active(self) -> ActiveSession | None:
        """Return the currently active session, or ``None``."""
        path = self.active_pointer_path
        if not path.exists():
            return None
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return None
        sid = data.get("session_id")
        if not isinstance(sid, str) or not sid:
            return None
        raw_label = data.get("label")
        raw_started = data.get("started_at")
        return ActiveSession(
            session_id=sid,
            label=raw_label if isinstance(raw_label, str) else None,
            started_at=raw_started if isinstance(raw_started, str) else "",
        )

    # ---- discovery ----------------------------------------------------

    def list_sessions(self) -> list[SessionInfo]:
        """All sessions on disk, oldest first (by ``started_at``).

        Corrupt ``session.toml`` files are silently skipped — the goal
        is that a broken session never breaks ``feedback list`` for the
        others.
        """
        if not self.root.exists():
            return []
        out: list[SessionInfo] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            info = self.load_info(child.name)
            if info is not None:
                out.append(info)
        # Secondary sort on session_id so two sessions started in the
        # same second (whole-second timestamp resolution) still get a
        # deterministic order — matters for the summary "pick the most
        # recent session" fallback path.
        out.sort(key=lambda i: (i.started_at, i.session_id))
        return out

    def load_info(self, session_id: str) -> SessionInfo | None:
        path = self.session_info_path(session_id)
        if not path.exists():
            return None
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return None
        try:
            return SessionInfo.model_validate(data)
        except Exception:
            return None

    def load_events(self, session_id: str) -> list[InvocationEvent]:
        """Parse the session's JSONL, skipping malformed lines.

        Malformed lines are dropped rather than raising — one bad append
        (e.g. a partial write from a killed process) shouldn't nuke the
        rest of the session's evidence.
        """
        path = self.events_path(session_id)
        if not path.exists():
            return []
        out: list[InvocationEvent] = []
        try:
            text = path.read_text()
        except OSError:
            return []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("kind") != "invocation":
                # v1 only knows how to interpret invocation events;
                # future collectors will each get their own parser.
                continue
            try:
                out.append(InvocationEvent.model_validate(payload))
            except Exception:
                continue
        return out

    # ---- appending events --------------------------------------------

    def append_event(self, session_id: str, event: InvocationEvent) -> None:
        """Append one JSONL line. Creates the session dir if missing."""
        self.session_dir(session_id).mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with self.events_path(session_id).open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---- internals ----------------------------------------------------

    def _write_info(self, info: SessionInfo) -> None:
        self.session_dir(info.session_id).mkdir(parents=True, exist_ok=True)
        doc = tomlkit.document()
        for key, value in info.model_dump(mode="json").items():
            if value is None:
                # tomlkit has no `null`; omit the key so `stopped_at`
                # simply isn't present until the session ends.
                continue
            doc[key] = value
        self.session_info_path(info.session_id).write_text(tomlkit.dumps(doc))

    def _write_active(self, info: SessionInfo) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        doc = tomlkit.document()
        doc["session_id"] = info.session_id
        if info.label is not None:
            doc["label"] = info.label
        doc["started_at"] = info.started_at
        self.active_pointer_path.write_text(tomlkit.dumps(doc))

    def _clear_active(self) -> None:
        path = self.active_pointer_path
        if path.exists():
            with contextlib.suppress(OSError):
                path.unlink()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def default_store() -> SessionStore:
    """Return a store rooted at the XDG state dir. Cheap; safe to call often."""
    return SessionStore()


def record_invocation_safe(
    argv: list[str],
    *,
    exit_code: int,
    wall_time_ms: int,
    cwd: str | None = None,
    store: SessionStore | None = None,
) -> None:
    """Best-effort recorder — never raises.

    Wraps :meth:`SessionStore.append_event` in a broad ``except Exception``
    because this runs on the exit path of *every* clickcast invocation
    when a session is active. If the JSONL write hits an OSError (disk
    full, permission denied, whatever) the user's CLI command must still
    exit cleanly with its real exit code — losing one line of session
    evidence is fine; losing the actual command output is not.
    """
    try:
        s = store if store is not None else default_store()
        current = s.active()
        if current is None:
            return
        event = InvocationEvent(
            kind="invocation",
            ts=_now_iso(),
            argv=list(argv),
            exit_code=int(exit_code),
            wall_time_ms=max(0, int(wall_time_ms)),
            cwd=cwd if cwd is not None else os.getcwd(),
            git_rev=_git_rev(cwd),
        )
        s.append_event(current.session_id, event)
    except Exception:
        # Deliberately swallowed — see docstring.
        return


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Whole-second ISO-8601 UTC. Whole seconds keep summary output
    stable across the fractional-microsecond noise that would otherwise
    break the byte-determinism test."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_session_id() -> str:
    """Sortable-by-time, unique, path-safe session id.

    Prefix with a UTC timestamp so ``ls`` returns sessions in the order
    they were created — matches the ordering the summary/list commands
    want. Suffix with a short uuid4 fragment for uniqueness in the case
    two sessions start in the same second (e.g. two shells racing).
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{stamp}-{suffix}"


def _git_rev(cwd: str | None) -> str | None:
    """Return the current git HEAD short-sha, or None.

    Deliberately avoids ``subprocess`` — reading ``.git/HEAD`` +
    ``.git/refs/…`` is faster and doesn't spawn a process on every
    single CLI invocation (which would compound with clickcast's own
    startup cost). Missing / detached / broken git states return None.
    """
    try:
        start = Path(cwd) if cwd else Path.cwd()
    except OSError:
        return None
    for candidate in (start, *start.parents):
        head = candidate / ".git" / "HEAD"
        if not head.exists():
            continue
        try:
            content = head.read_text().strip()
        except OSError:
            return None
        if content.startswith("ref: "):
            ref = content[len("ref: ") :].strip()
            ref_path = candidate / ".git" / ref
            if ref_path.exists():
                try:
                    return ref_path.read_text().strip()[:12] or None
                except OSError:
                    return None
            # Packed refs — fall back to reading them.
            packed = candidate / ".git" / "packed-refs"
            if packed.exists():
                try:
                    for line in packed.read_text().splitlines():
                        if line.endswith(" " + ref):
                            return line.split()[0][:12]
                except OSError:
                    return None
            return None
        # Detached HEAD — content IS the sha.
        return content[:12] or None
    return None
