"""URL helpers for BFS traversal — dedup + same-origin gating."""

from __future__ import annotations

from urllib.parse import urldefrag, urlparse

__all__ = ["is_same_origin", "normalize_url"]


def normalize_url(url: str) -> str:
    """Strip the URL fragment. Used for dedup — ``/x#foo`` and ``/x#bar`` are the
    same page for tour purposes.

    Trailing slash on the path is preserved to distinguish ``/`` from ``/index``.
    Query strings are also preserved (``/search?q=a`` and ``/search?q=b`` are
    different pages).
    """
    clean, _ = urldefrag(url)
    return clean


def is_same_origin(a: str, b: str) -> bool:
    """True if ``a`` and ``b`` share scheme + host + port."""
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)
