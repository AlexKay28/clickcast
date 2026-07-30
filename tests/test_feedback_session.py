"""Tests for the #124 v1 feedback session substrate.

Covers:

- Session lifecycle: start → stop → files land on disk in the right layout.
- JSONL round-trip: written events read back byte-identical.
- Summary determinism: same input → byte-identical Markdown + JSON output.
- CLI subcommands under ``clickcast feedback …`` via ``CliRunner``.
- Zero-crash resilience: a missing / corrupt session file must never
  make the recorder raise or make ``feedback list``/``summary`` blow up
  on siblings.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from clickcast.cli import app
from clickcast.feedback.session.storage import (
    InvocationEvent,
    SessionStore,
    record_invocation_safe,
)
from clickcast.feedback.session.summary import (
    render_json,
    render_markdown,
    summarize,
)


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    """A store rooted at ``tmp_path`` so no test touches the real XDG dir."""
    return SessionStore(root=tmp_path / "feedback")


@pytest.fixture
def env_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[SessionStore, Path]:
    """Store + env override — used by CLI tests that resolve the store
    via ``default_store()`` inside the subcommand functions.

    Setting ``CLICKCAST_FEEDBACK_ROOT`` is what tests should do in real
    usage too; the env-var escape hatch is deliberately documented in
    :func:`clickcast.feedback.session.storage._default_state_root`.
    """
    root = tmp_path / "feedback"
    monkeypatch.setenv("CLICKCAST_FEEDBACK_ROOT", str(root))
    return SessionStore(root=root), root


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_start_creates_session_and_active_pointer(self, store: SessionStore) -> None:
        info = store.start(label="my label")
        assert info.session_id
        assert info.label == "my label"
        assert info.started_at
        assert info.stopped_at is None
        # Files land where the design says they do.
        assert store.session_info_path(info.session_id).exists()
        assert store.active_pointer_path.exists()
        # Active pointer round-trips back to this session.
        active = store.active()
        assert active is not None
        assert active.session_id == info.session_id
        assert active.label == "my label"

    def test_start_twice_returns_existing_session(self, store: SessionStore) -> None:
        first = store.start(label="one")
        second = store.start(label="two")
        # No second session should be created — the first is still active.
        assert first.session_id == second.session_id
        assert second.label == "one"

    def test_stop_populates_stopped_at_and_clears_pointer(self, store: SessionStore) -> None:
        info = store.start()
        stopped = store.stop()
        assert stopped is not None
        assert stopped.session_id == info.session_id
        assert stopped.stopped_at is not None
        assert not store.active_pointer_path.exists()
        # Round-trip: load_info sees stopped_at now.
        reloaded = store.load_info(info.session_id)
        assert reloaded is not None
        assert reloaded.stopped_at == stopped.stopped_at

    def test_stop_with_no_active_returns_none(self, store: SessionStore) -> None:
        assert store.stop() is None

    def test_list_returns_all_recorded_sessions(self, store: SessionStore) -> None:
        a = store.start(label="first")
        store.stop()
        b = store.start(label="second")
        store.stop()
        ids = {i.session_id for i in store.list_sessions()}
        assert ids == {a.session_id, b.session_id}


class TestJsonlRoundtrip:
    def test_append_and_load(self, store: SessionStore) -> None:
        info = store.start()
        e1 = InvocationEvent(ts="2026-07-30T10:00:00Z", argv=["auto", "https://x"], exit_code=0)
        e2 = InvocationEvent(ts="2026-07-30T10:01:00Z", argv=["run", "tour.yml"], exit_code=1)
        store.append_event(info.session_id, e1)
        store.append_event(info.session_id, e2)
        loaded = store.load_events(info.session_id)
        assert [e.argv for e in loaded] == [["auto", "https://x"], ["run", "tour.yml"]]
        assert [e.exit_code for e in loaded] == [0, 1]

    def test_malformed_lines_are_skipped(self, store: SessionStore) -> None:
        info = store.start()
        # Write valid + garbage + valid — the garbage must NOT poison the read.
        good = InvocationEvent(ts="2026-07-30T10:00:00Z", argv=["auto"], exit_code=0)
        store.append_event(info.session_id, good)
        with store.events_path(info.session_id).open("a") as f:
            f.write("this is not json\n")
        store.append_event(info.session_id, good)
        loaded = store.load_events(info.session_id)
        assert len(loaded) == 2


class TestResilience:
    def test_missing_session_dir_no_crash(self, store: SessionStore) -> None:
        # The store's root doesn't even exist yet — everything should
        # return empty rather than raise.
        assert store.list_sessions() == []
        assert store.load_info("nope") is None
        assert store.load_events("nope") == []
        assert store.active() is None
        assert store.stop() is None

    def test_corrupt_active_pointer_returns_none(self, store: SessionStore) -> None:
        store.root.mkdir(parents=True, exist_ok=True)
        store.active_pointer_path.write_text("this is not toml === {}\x00")
        assert store.active() is None

    def test_corrupt_session_info_skipped_in_list(self, store: SessionStore) -> None:
        good = store.start(label="ok")
        store.stop()
        # Manufacture a broken session directory alongside the good one.
        bad_dir = store.root / "20260101T000000Z-deadbeef"
        bad_dir.mkdir(parents=True)
        (bad_dir / "session.toml").write_text("garbage = = =")
        listing = store.list_sessions()
        assert [i.session_id for i in listing] == [good.session_id]

    def test_record_invocation_safe_swallows_all_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No active session — should just return silently, not touch disk.
        s = SessionStore(root=tmp_path / "feedback")
        record_invocation_safe(["auto", "x"], exit_code=0, wall_time_ms=1, store=s)
        assert not s.active_pointer_path.exists()

        # With an active session but a broken write path, must not raise.
        s.start()
        # Replace events_path so the writer targets an unopenable location.
        original = s.events_path

        def _broken(session_id: str) -> Path:
            # A path whose parent is a regular file — mkdir fails, open fails.
            broken_parent = tmp_path / "not-a-dir"
            broken_parent.write_text("i am a file")
            return broken_parent / "events.jsonl"

        monkeypatch.setattr(s, "events_path", _broken)
        # Must not raise.
        record_invocation_safe(["auto"], exit_code=0, wall_time_ms=1, store=s)
        # Restore for cleanliness.
        monkeypatch.setattr(s, "events_path", original)


class TestRecordInvocation:
    def test_appends_when_session_active(self, store: SessionStore) -> None:
        info = store.start()
        record_invocation_safe(
            ["auto", "https://example.com"],
            exit_code=0,
            wall_time_ms=42,
            cwd="/tmp",
            store=store,
        )
        events = store.load_events(info.session_id)
        assert len(events) == 1
        assert events[0].argv == ["auto", "https://example.com"]
        assert events[0].exit_code == 0
        assert events[0].wall_time_ms == 42
        assert events[0].cwd == "/tmp"

    def test_no_op_when_no_active_session(self, store: SessionStore) -> None:
        record_invocation_safe(["auto"], exit_code=0, wall_time_ms=1, store=store)
        assert store.list_sessions() == []


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _fixture_events() -> list[InvocationEvent]:
    """Deterministic event list used by every summary test."""
    return [
        InvocationEvent(ts="2026-07-30T10:00:00Z", argv=["auto", "https://a"], exit_code=0),
        InvocationEvent(ts="2026-07-30T10:01:00Z", argv=["auto", "https://b"], exit_code=1),
        InvocationEvent(ts="2026-07-30T10:02:00Z", argv=["auto", "https://c"], exit_code=1),
        InvocationEvent(ts="2026-07-30T10:03:00Z", argv=["run", "tour.yml"], exit_code=0),
        InvocationEvent(ts="2026-07-30T10:04:00Z", argv=["skill"], exit_code=0),
    ]


class TestSummary:
    def test_counts_and_patterns(self, store: SessionStore) -> None:
        info = store.start(label="fixture")
        for e in _fixture_events():
            store.append_event(info.session_id, e)
        stopped = store.stop()
        assert stopped is not None
        summary = summarize(stopped, store.load_events(info.session_id))
        assert summary.invocation_count == 5
        assert summary.failed_count == 2
        assert summary.top_patterns[0] == ("clickcast auto", 3)
        # Sorted by pattern for stability.
        assert summary.failed_invocations == (("clickcast auto", 2),)

    def test_markdown_deterministic(self, store: SessionStore) -> None:
        info = store.start(label="fixture")
        for e in _fixture_events():
            store.append_event(info.session_id, e)
        stopped = store.stop()
        assert stopped is not None
        summary = summarize(stopped, store.load_events(info.session_id))
        first = render_markdown(summary)
        second = render_markdown(summary)
        assert first == second
        # And the JSON side.
        j1 = render_json(summary)
        j2 = render_json(summary)
        assert j1 == j2

    def test_markdown_contains_expected_sections(self, store: SessionStore) -> None:
        info = store.start(label="fixture")
        for e in _fixture_events():
            store.append_event(info.session_id, e)
        stopped = store.stop()
        assert stopped is not None
        md = render_markdown(summarize(stopped, store.load_events(info.session_id)))
        assert "# Feedback session" in md
        assert "## Overview" in md
        assert "## Most-frequent commands" in md
        assert "## Failed invocations" in md

    def test_json_contains_top_patterns_shape(self, store: SessionStore) -> None:
        info = store.start(label="fixture")
        for e in _fixture_events():
            store.append_event(info.session_id, e)
        stopped = store.stop()
        assert stopped is not None
        import json as _json

        payload = _json.loads(render_json(summarize(stopped, store.load_events(info.session_id))))
        assert payload["invocation_count"] == 5
        assert payload["failed_count"] == 2
        assert payload["top_patterns"][0] == {"pattern": "clickcast auto", "count": 3}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    runner = CliRunner()

    def test_start_stop_status_flow(self, env_store: tuple[SessionStore, Path]) -> None:
        store, _ = env_store
        # No session yet.
        r = self.runner.invoke(app, ["feedback", "status"])
        assert r.exit_code == 0
        assert "no active session" in r.stdout

        # Start.
        r = self.runner.invoke(app, ["feedback", "start", "--label", "test-run"])
        assert r.exit_code == 0
        assert "session started" in r.stdout
        assert store.active() is not None

        # Status now shows active.
        r = self.runner.invoke(app, ["feedback", "status"])
        assert r.exit_code == 0
        assert "active session" in r.stdout
        assert "test-run" in r.stdout

        # Stop.
        r = self.runner.invoke(app, ["feedback", "stop"])
        assert r.exit_code == 0
        assert "session stopped" in r.stdout
        assert store.active() is None

    def test_stop_with_no_active_exits_nonzero(self, env_store: tuple[SessionStore, Path]) -> None:
        r = self.runner.invoke(app, ["feedback", "stop"])
        assert r.exit_code == 1
        assert "no active session" in r.stdout

    def test_list_shows_recorded_sessions(self, env_store: tuple[SessionStore, Path]) -> None:
        store, _ = env_store
        info = store.start(label="alpha")
        store.stop()
        r = self.runner.invoke(app, ["feedback", "list"])
        assert r.exit_code == 0
        assert info.session_id in r.stdout
        assert "alpha" in r.stdout

    def test_summary_uses_most_recent_when_no_flag(
        self, env_store: tuple[SessionStore, Path]
    ) -> None:
        store, _ = env_store
        info = store.start(label="cli-summary")
        for e in _fixture_events():
            store.append_event(info.session_id, e)
        store.stop()
        r = self.runner.invoke(app, ["feedback", "summary"])
        assert r.exit_code == 0
        assert "# Feedback session" in r.stdout
        assert "clickcast auto" in r.stdout

    def test_summary_json_flag_is_deterministic(self, env_store: tuple[SessionStore, Path]) -> None:
        store, _ = env_store
        info = store.start(label="cli-summary")
        for e in _fixture_events():
            store.append_event(info.session_id, e)
        store.stop()
        a = self.runner.invoke(app, ["feedback", "summary", "--json"])
        b = self.runner.invoke(app, ["feedback", "summary", "--json"])
        assert a.exit_code == 0 == b.exit_code
        assert a.stdout == b.stdout

    def test_summary_with_no_sessions_exits_nonzero(
        self, env_store: tuple[SessionStore, Path]
    ) -> None:
        r = self.runner.invoke(app, ["feedback", "summary"])
        assert r.exit_code == 1

    def test_help_lists_subcommands(self, env_store: tuple[SessionStore, Path]) -> None:
        r = self.runner.invoke(app, ["feedback", "--help"])
        assert r.exit_code == 0
        for cmd in ("start", "stop", "status", "list", "summary"):
            assert cmd in r.stdout
