from __future__ import annotations

import pytest

from clickcast.core.session import EngineNotInstalledError, Session, hostname_matches

pytestmark = pytest.mark.unit


async def test_aenter_raises_clear_error_when_engine_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-flight check (see clickcast.core.engines) must fire before
    Playwright even starts, so a missing browser never surfaces as a raw
    "Executable doesn't exist" traceback — every entry point (CLI, Python
    API, MCP server) goes through Session.__aenter__, so this one check
    covers all of them."""
    monkeypatch.setattr("clickcast.core.session.find_installed_engine", lambda engine: None)
    with pytest.raises(EngineNotInstalledError, match="chromium isn't installed"):
        async with Session(engine="chromium"):
            pass


async def test_aenter_proceeds_past_preflight_when_engine_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the engine IS found, __aenter__ must not raise
    EngineNotInstalledError — proven by mocking Playwright's own startup to
    raise a distinct sentinel, so a real browser is never needed here (real
    end-to-end launch coverage lives in TestSessionIntegration below)."""
    from pathlib import Path

    class _Sentinel(Exception):
        pass

    monkeypatch.setattr(
        "clickcast.core.session.find_installed_engine",
        lambda engine: (Path("/fake/chrome"), "executable"),
    )

    def _boom() -> None:
        raise _Sentinel

    monkeypatch.setattr("clickcast.core.session.async_playwright", _boom)
    with pytest.raises(_Sentinel):
        async with Session(engine="chromium"):
            pass


# `_parse_viewport` was removed in the #96 refactor — parsing lives in
# `clickcast.core.viewport.Viewport.parse` now, covered by
# `tests/test_viewport.py`. The session-side behaviour is exercised in
# `test_context_kwargs_*` below.


def test_page_property_raises_when_closed() -> None:
    sess = Session()
    with pytest.raises(RuntimeError, match="not open"):
        _ = sess.page


def test_context_kwargs_viewport_string() -> None:
    sess = Session(viewport="640x480")
    assert sess._context_kwargs() == {"viewport": {"width": 640, "height": 480}}


def test_context_kwargs_dark_and_lang_and_headers() -> None:
    sess = Session(
        dark=True,
        lang="fr-FR",
        extra_http_headers={"X-Foo": "bar"},
    )
    kwargs = sess._context_kwargs()
    assert kwargs == {
        "color_scheme": "dark",
        "locale": "fr-FR",
        "extra_http_headers": {"X-Foo": "bar"},
    }


def test_context_kwargs_proxy_string_and_dict() -> None:
    assert Session(proxy="http://proxy.example:8080")._context_kwargs() == {
        "proxy": {"server": "http://proxy.example:8080"}
    }
    assert Session(proxy={"server": "http://p", "username": "u"})._context_kwargs() == {
        "proxy": {"server": "http://p", "username": "u"}
    }


class TestInsecureAndScopedHeaders:
    """#166: ignore_https_errors + header_host route-scoping."""

    def test_ignore_https_errors_flows_to_context(self) -> None:
        assert Session(ignore_https_errors=True)._context_kwargs() == {"ignore_https_errors": True}

    def test_ignore_https_errors_default_absent(self) -> None:
        """Default False means we don't send the kwarg at all — keeps the
        current chromium hardening behaviour untouched unless opted in."""
        assert "ignore_https_errors" not in Session()._context_kwargs()

    def test_headers_global_when_no_host(self) -> None:
        """Without `header_host`, headers land in context.extra_http_headers
        (the pre-#166 behaviour) so no route interceptor is needed."""
        sess = Session(extra_http_headers={"Authorization": "Bearer x"})
        assert sess._context_kwargs() == {"extra_http_headers": {"Authorization": "Bearer x"}}

    def test_headers_absent_from_context_when_scoped(self) -> None:
        """With `header_host` set, the context-wide path MUST be silent —
        the route interceptor injects headers per-request instead. Leaking
        the header both places would defeat scoping."""
        sess = Session(
            extra_http_headers={"Authorization": "Bearer x"},
            header_host="internal.example.com",
        )
        assert "extra_http_headers" not in sess._context_kwargs()


class TestHostnameMatches:
    """#166: dotted-suffix hostname match. Substring `in` was rejected
    because ``host=".net"`` would happily match ``cdn.example.net``."""

    @pytest.mark.parametrize(
        ("url", "host", "expected"),
        [
            # Exact hostname match.
            ("https://internal.example.com/a", "internal.example.com", True),
            # Dotted-suffix match: subdomain matches the parent.
            ("https://api.internal.example.com/a", "internal.example.com", True),
            # Leading-dot suffix works the same as the bare form.
            ("https://api.internal.example.com/a", ".internal.example.com", True),
            # Bare base domain matches itself with a leading-dot host.
            ("https://internal.example.com/a", ".internal.example.com", True),
            # NEGATIVE: a suffix must sit on a dot boundary — the whole
            # point of not doing substring matching.
            ("https://cdn.example.net/a", ".net", False),
            ("https://malicious-internal.example.com/a", "internal.example.com", False),
            # Different host altogether.
            ("https://other.example.com/a", "internal.example.com", False),
            # Empty inputs never match.
            ("https://internal.example.com/a", "", False),
            ("", "internal.example.com", False),
            # Case-insensitive.
            ("https://INTERNAL.EXAMPLE.COM/a", "internal.example.com", True),
        ],
    )
    def test_matcher(self, url: str, host: str, expected: bool) -> None:
        assert hostname_matches(url, host) is expected


@pytest.mark.integration
class TestSessionIntegration:
    """Requires `playwright install chromium`."""

    pytestmark = pytest.mark.integration

    async def test_open_screenshot_close(self) -> None:
        async with Session(viewport=(400, 300)) as sess:
            await sess.goto("data:text/html,<h1>hi</h1>", wait="load")
            png = await sess.screenshot()
        assert png.startswith(b"\x89PNG\r\n\x1a\n"), "expected PNG magic bytes"
        assert len(png) > 100

    async def test_wait_numeric_and_selector(self) -> None:
        async with Session(viewport=(400, 300)) as sess:
            await sess.goto(
                "data:text/html,<div id=x>hi</div>",
                wait="#x",
            )
            await sess.goto("data:text/html,<p>y</p>", wait=0.05)

    async def test_teardown_on_exception(self) -> None:
        with pytest.raises(ValueError, match="boom"):
            async with Session(viewport=(320, 240)) as sess:
                await sess.goto("data:text/html,<p>hi</p>", wait="load")
                raise ValueError("boom")


@pytest.mark.integration
class TestScopedHeaderDeliveryIntegration:
    """#166: with ``header_host`` set, the auth header must reach requests
    to the allowed origin AND stay off requests to any other origin.

    Test setup runs one HTTP server bound to 127.0.0.1 and reachable via
    two hostnames (``127.0.0.1`` and ``localhost``). The page loaded from
    ``127.0.0.1`` triggers a subresource fetch to ``localhost`` — the
    server records the incoming ``Authorization`` header per request path
    so we can assert scoping worked.
    """

    pytestmark = pytest.mark.integration

    async def test_header_only_reaches_allowed_host(self) -> None:
        import socket
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = int(s.getsockname()[1])

        # Path → whether it saw an Authorization header.
        seen: dict[str, str | None] = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen[self.path] = self.headers.get("Authorization")
                if self.path == "/main":
                    body = (
                        b"<html><body>"
                        # Force a subresource fetch to the *other* hostname
                        # (localhost) so we can observe whether the auth
                        # header leaks across origins.
                        b"<img src='http://localhost:" + str(port).encode() + b"/leaked.png' />"
                        b"</body></html>"
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            async with Session(
                extra_http_headers={"Authorization": "Bearer secret"},
                header_host="127.0.0.1",
            ) as sess:
                await sess.goto(f"http://127.0.0.1:{port}/main", wait="load")
                # The image load is fire-and-forget from `load`; give it a
                # beat to fetch. 100ms is generous for loopback.
                await sess.wait(0.2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

        # Main page (127.0.0.1) got the header.
        assert seen.get("/main") == "Bearer secret"
        # Subresource (localhost) did NOT get the header — this is the
        # whole point of --header-host. Regression here means bearer
        # tokens are leaking to third-party origins.
        assert seen.get("/leaked.png") is None
