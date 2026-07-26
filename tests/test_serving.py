"""Tests for :mod:`clickcast.serving` + :meth:`Reel.serve_dir`.

Kept dependency-free (stdlib only) so the QoL helper works even when
Playwright isn't installed. Uses ``urllib`` for HTTP requests instead of
adding ``httpx``/``requests`` to the dev deps.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from clickcast import Reel
from clickcast.serving import serve_directory

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """Minimal static site: index + a nested asset."""
    (tmp_path / "index.html").write_text("<!doctype html><title>ix</title>hello", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "asset.txt").write_text("nested-asset-body", encoding="utf-8")
    return tmp_path


def _get(url: str, *, timeout: float = 2.0) -> tuple[int, bytes]:
    with urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read()


def _port_from_url(url: str) -> int:
    # url like http://127.0.0.1:54321
    return int(url.rsplit(":", 1)[1])


# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------


class TestServeDirectory:
    def test_yields_loopback_url_with_scheme_host_port(self, site: Path) -> None:
        with serve_directory(site) as url:
            assert url.startswith("http://127.0.0.1:")
            # Port is an int and non-zero.
            assert _port_from_url(url) > 0

    def test_request_reaches_index_html(self, site: Path) -> None:
        with serve_directory(site) as url:
            status, body = _get(url + "/")
            assert status == 200
            assert b"hello" in body

    def test_request_reaches_nested_asset(self, site: Path) -> None:
        with serve_directory(site) as url:
            status, body = _get(url + "/sub/asset.txt")
            assert status == 200
            assert body == b"nested-asset-body"

    def test_auto_port_when_none(self, site: Path) -> None:
        """port=None -> OS picks a free port; different invocations may
        differ but each yielded URL must be reachable."""
        with serve_directory(site, port=None) as url:
            port = _port_from_url(url)
            assert port > 0
            status, _ = _get(url + "/")
            assert status == 200

    def test_explicit_port_is_honored(self, site: Path) -> None:
        # Grab a free port up front, then ask serve_directory to bind it.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            requested = int(s.getsockname()[1])
        with serve_directory(site, port=requested) as url:
            assert _port_from_url(url) == requested

    def test_single_threaded_variant_still_serves(self, site: Path) -> None:
        with serve_directory(site, threading=False) as url:
            status, body = _get(url + "/")
            assert status == 200
            assert b"hello" in body


# ------------------------------------------------------------------
# Teardown
# ------------------------------------------------------------------


class TestTeardown:
    def test_port_refuses_connections_after_exit(self, site: Path) -> None:
        with serve_directory(site) as url:
            port = _port_from_url(url)
            # Sanity: while alive, requests succeed.
            status, _ = _get(url + "/")
            assert status == 200
        # After exit, the socket is closed. Poll briefly because the
        # accept loop takes a beat to unwind on some kernels.
        deadline = time.monotonic() + 2.0
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    # Still accepting — wait and retry.
                    last_exc = None
                    time.sleep(0.05)
                    continue
            except (ConnectionRefusedError, OSError) as exc:
                last_exc = exc
                break
        assert isinstance(last_exc, (ConnectionRefusedError, OSError)), (
            f"port {port} still accepting connections after context exit"
        )

    def test_urlopen_after_exit_raises(self, site: Path) -> None:
        with serve_directory(site) as url:
            status, _ = _get(url + "/")
            assert status == 200
        # After teardown, urlopen wraps ConnectionRefusedError in URLError.
        with pytest.raises((URLError, ConnectionRefusedError, OSError)):
            _get(url + "/", timeout=1.0)


# ------------------------------------------------------------------
# Validation of input path
# ------------------------------------------------------------------


class TestPathValidation:
    def test_missing_path_raises_filenotfound(self, tmp_path: Path) -> None:
        with (
            pytest.raises(FileNotFoundError),
            serve_directory(tmp_path / "does-not-exist"),
        ):
            pass

    def test_path_is_a_file_raises_notadirectory(self, tmp_path: Path) -> None:
        p = tmp_path / "afile.txt"
        p.write_text("x", encoding="utf-8")
        with pytest.raises(NotADirectoryError), serve_directory(p):
            pass


# ------------------------------------------------------------------
# Bind safety
# ------------------------------------------------------------------


class TestBind:
    def test_default_bind_is_loopback(self, site: Path) -> None:
        with serve_directory(site) as url:
            assert "127.0.0.1" in url

    def test_loopback_bind_not_reachable_from_nonloopback(self, site: Path) -> None:
        """A server bound to 127.0.0.1 must refuse connections addressed to
        a non-loopback local IP.

        Skipped on hosts that don't have a routable non-loopback IPv4 (CI
        containers often don't) — the assertion isn't meaningful without
        one and we don't want a flaky skip vs. fail heuristic.
        """
        # Best-effort: pick a non-loopback IP by opening a UDP socket to a
        # public address (no packets sent) and reading the local addr.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("8.8.8.8", 80))
                local_ip = probe.getsockname()[0]
        except OSError:
            pytest.skip("no routable interface — can't test non-loopback isolation")
        if local_ip == "127.0.0.1" or local_ip.startswith("127."):
            pytest.skip(f"only loopback available ({local_ip}) — nothing to isolate against")

        with serve_directory(site) as url:
            port = _port_from_url(url)
            with (
                pytest.raises((ConnectionRefusedError, TimeoutError, OSError)),
                socket.create_connection((local_ip, port), timeout=0.5),
            ):
                pass


# ------------------------------------------------------------------
# Reel.serve_dir classmethod (thin wrapper)
# ------------------------------------------------------------------


class TestReelServeDir:
    def test_classmethod_is_a_context_manager(self, site: Path) -> None:
        # Callable, and returns a context manager yielding a URL.
        with Reel.serve_dir(site) as url:
            assert url.startswith("http://127.0.0.1:")
            status, body = _get(url + "/")
            assert status == 200
            assert b"hello" in body

    def test_classmethod_teardown_frees_port(self, site: Path) -> None:
        with Reel.serve_dir(site) as url:
            port = _port_from_url(url)
        # After exit, connections refused (poll briefly for accept-loop unwind).
        deadline = time.monotonic() + 2.0
        refused = False
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    time.sleep(0.05)
                    continue
            except (ConnectionRefusedError, OSError):
                refused = True
                break
        assert refused, f"port {port} still accepting after Reel.serve_dir exit"

    def test_classmethod_forwards_kwargs(self, site: Path) -> None:
        # Passing threading=False and an explicit port still works end-to-end.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            requested = int(s.getsockname()[1])
        with Reel.serve_dir(site, port=requested, threading=False) as url:
            assert _port_from_url(url) == requested
            status, _ = _get(url + "/")
            assert status == 200
