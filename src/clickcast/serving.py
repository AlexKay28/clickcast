"""Local static file server as a context manager.

QoL helper for the pre-push iteration loop — build a static site, hand its
directory to ``serve_directory``, drive a browser against the yielded URL,
and let the ``with`` block tear the server down when it exits (no more
"forgot to kill port 8091" three-cli-invocations later).

Example::

    from clickcast import Reel
    from clickcast.serving import serve_directory

    with serve_directory("./public") as url:
        Reel(url).goto().click(".chip").save("out.gif")
    # server is gone; the port is free again.

Design notes:

- Uses :class:`http.server.ThreadingHTTPServer` by default so parallel
  requests from a headless browser don't queue on a single-threaded server.
- Default bind is ``127.0.0.1`` (loopback only) — safer than ``0.0.0.0``.
- When ``port`` is ``None`` the kernel picks a free port (``bind port 0``);
  the yielded URL contains the actual assigned port.
- Shutdown is best-effort: :meth:`~socketserver.BaseServer.shutdown` stops
  the serve loop, :meth:`~socketserver.BaseServer.server_close` releases
  the socket, and the background thread is joined with a short timeout so
  the caller isn't blocked if a handler is wedged.
"""

from __future__ import annotations

import socket
import threading as _threading  # aliased so ``threading`` can be a kw-only arg
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

__all__ = ["serve_directory"]


class _QuietHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that stays silent on stderr.

    ``SimpleHTTPRequestHandler`` prints every request to stderr by default;
    that noise makes ``pytest``/agent output unreadable when the server is
    used from a test or scripted iteration loop.
    """

    def log_message(self, format: str, *args: Any) -> None:
        # Intentionally silent — callers own their logging.
        return


def _pick_free_port(bind: str) -> int:
    """Ask the OS for an ephemeral free port on ``bind``.

    Bound-then-closed sockets on Linux enter ``TIME_WAIT`` but the kernel
    will not reissue the same port in the microseconds between this
    ``close()`` and the ``HTTPServer`` bind that follows, so this
    two-step is safe in practice for the intended dev-iteration use case.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((bind, 0))
        return int(s.getsockname()[1])


def _wait_until_ready(host: str, port: int, timeout: float = 3.0) -> None:
    """Block until ``host:port`` accepts a TCP connection.

    Serving-thread startup is asynchronous; without this wait, the first
    request from the caller can race the accept loop and see
    ``ConnectionRefusedError`` on a technically-live server.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.01)
    raise RuntimeError(f"serve_directory: server not ready on {host}:{port} within {timeout}s")


@contextmanager
def serve_directory(
    path: str | Path,
    *,
    port: int | None = None,
    bind: str = "127.0.0.1",
    threading: bool = True,
) -> Iterator[str]:
    """Serve ``path`` over HTTP for the duration of the ``with`` block.

    Yields the base URL (e.g. ``"http://127.0.0.1:54321"``). The directory
    is served as the document root — a request for ``/`` maps to
    ``path/index.html`` when present.

    Parameters:
        path: Filesystem directory to serve as the document root. Must exist.
        port: TCP port to bind. ``None`` (default) auto-picks a free port.
        bind: Interface to bind on. ``"127.0.0.1"`` (default) is loopback
            only — safe for dev iteration. Use ``"0.0.0.0"`` to expose to
            the LAN (opt-in).
        threading: When ``True`` (default) uses
            :class:`http.server.ThreadingHTTPServer` so parallel browser
            requests don't queue on a single-threaded server. ``False``
            falls back to the plain :class:`http.server.HTTPServer` for
            deterministic single-threaded semantics.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        NotADirectoryError: ``path`` exists but is not a directory.
        OSError: The requested ``port`` is already in use.
    """
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"serve_directory: {root} does not exist")
    if not root.is_dir():
        raise NotADirectoryError(f"serve_directory: {root} is not a directory")

    bound_port = _pick_free_port(bind) if port is None else port
    # ``SimpleHTTPRequestHandler`` takes ``directory`` since 3.7; bind it via
    # ``functools.partial`` so the handler stays a class the server can
    # instantiate per-request.
    handler = partial(_QuietHandler, directory=str(root))
    server_cls: type[HTTPServer] = ThreadingHTTPServer if threading else HTTPServer
    server = server_cls((bind, bound_port), handler)
    # Read the actually-bound port back off the socket — if the caller
    # asked for auto-port, ``bound_port`` was just a best-effort hint from
    # ``_pick_free_port``; the server may (rarely) get a different one.
    actual_port = int(server.server_address[1])

    thread = _threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name=f"clickcast-serve-{actual_port}",
    )
    thread.start()
    try:
        _wait_until_ready(bind, actual_port)
        yield f"http://{bind}:{actual_port}"
    finally:
        # ``shutdown()`` stops the ``serve_forever`` loop; ``server_close()``
        # releases the socket so the port is immediately reusable.
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
