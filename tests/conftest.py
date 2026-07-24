"""Global test config.

Provides:

- ``fixture_site_url``: a session-scoped `http.server` running
  ``tests/fixtures/site/`` on an ephemeral port so integration tests can
  drive a real multi-page browser tour without hitting the public internet.
- ``stub_environment``: patches every heavy dependency the auto engine
  touches (Session, Recorder, encode, annotate_frames_dir, _write_sidecar,
  ReportBuilder) so unit tests can invoke it without a real browser.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Generator
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests._stubs import FakeRecorder, FakeSession

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "site"


def _pick_port() -> int:
    """Ask the OS for a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_ready(host: str, port: int, timeout: float = 3.0) -> None:
    """Poll until the fixture server accepts connections. Setup only — never in tests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.01)
    raise RuntimeError(f"fixture server not ready on {host}:{port}")


class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler pinned to the fixture root and silenced."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(_FIXTURE_ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        # Silence per-request logging so pytest output stays clean.
        return


@pytest.fixture(scope="session")
def fixture_site_url() -> Generator[str, None, None]:
    """Base URL of the local fixture site — served once per test run."""
    port = _pick_port()
    server = HTTPServer(("127.0.0.1", port), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="fixture-http")
    thread.start()
    _wait_until_ready("127.0.0.1", port)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.fixture
def stub_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """Patch every heavy dependency the auto engine touches so tests can
    invoke it without a real browser. Returns the shared :class:`FakeSession`
    the engine will see; tests inspect its ``page`` for URL history +
    go_back calls.
    """
    fake_sess = FakeSession()

    class _SessCtor:
        def __init__(self, **_kwargs: Any) -> None:
            self._sess = fake_sess

        async def __aenter__(self) -> FakeSession:
            return self._sess

        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("clickcast.auto.Session", _SessCtor)

    class _FakeRecorderCtor:
        def __init__(self, **_kwargs: Any) -> None:
            self._rec = FakeRecorder(tmp_path)

        def __enter__(self) -> FakeRecorder:
            return self._rec

        def __exit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("clickcast.auto.Recorder", _FakeRecorderCtor)
    monkeypatch.setattr("clickcast.auto.annotate_frames_dir", MagicMock(return_value=0))
    monkeypatch.setattr(
        "clickcast.auto.encode",
        MagicMock(
            return_value=MagicMock(
                path=tmp_path / "reel.gif",
                format="gif",
                size_bytes=1024,
                duration_s=1.0,
                frame_count=10,
            )
        ),
    )
    monkeypatch.setattr("clickcast.auto._write_sidecar", MagicMock(return_value=None))
    monkeypatch.setattr("clickcast.auto.ReportBuilder", MagicMock)
    return fake_sess
