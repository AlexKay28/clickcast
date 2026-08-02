"""Regression tests for the page-scope discovery cache (see #151 PERF-1).

The auto engine used to re-query ``discover()`` on every request for the
element pool — a real cost on slow sites where each walk is 100 ms+.
``explore_page`` now creates a URL-keyed cache dict and threads it through
``_goto_and_discover`` (via ``_ensure_discovered``) so a same-URL re-entry
reuses the memoized pool. The cache lives only for one ``explore_page``
call — every new page starts cold, which is the invalidation boundary.

These tests lock in three properties:

- A cold cache calls ``discover()`` once and stores the result.
- A cached URL returns the stored pool without a second ``discover()``.
- A different URL against the same cache pays a fresh ``discover()``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clickcast.auto import _ensure_discovered
from clickcast.discovery import Element
from tests._stubs import FakeSession, make_element


@pytest.mark.asyncio
async def test_ensure_discovered_populates_cold_cache() -> None:
    """First call for a URL fetches from ``discover()`` and stores it."""
    sess = FakeSession()
    cache: dict[str, list[Element]] = {}
    pool = [make_element("A"), make_element("B")]

    with patch("clickcast.auto.discover", AsyncMock(return_value=pool)) as fake:
        result = await _ensure_discovered(
            sess=sess, url="https://x.com/a", click_budget=5, cache=cache
        )

    assert result == pool
    assert cache == {"https://x.com/a": pool}
    assert fake.await_count == 1


@pytest.mark.asyncio
async def test_ensure_discovered_reuses_cached_entry() -> None:
    """A second call for the same URL returns the cached pool without a
    fresh ``discover()`` — the whole point of PERF-1."""
    sess = FakeSession()
    pool = [make_element("A")]
    cache: dict[str, list[Element]] = {"https://x.com/a": pool}

    with patch("clickcast.auto.discover", AsyncMock(return_value=[])) as fake:
        result = await _ensure_discovered(
            sess=sess, url="https://x.com/a", click_budget=5, cache=cache
        )

    assert result is pool
    assert fake.await_count == 0


@pytest.mark.asyncio
async def test_ensure_discovered_keys_by_url() -> None:
    """A different URL against the same cache pays a fresh ``discover()``
    call and coexists with the prior entry — the URL is the cache key."""
    sess = FakeSession()
    pool_a = [make_element("A")]
    pool_b = [make_element("B"), make_element("C")]
    cache: dict[str, list[Element]] = {"https://x.com/a": pool_a}

    with patch("clickcast.auto.discover", AsyncMock(return_value=pool_b)) as fake:
        result = await _ensure_discovered(
            sess=sess, url="https://x.com/b", click_budget=5, cache=cache
        )

    assert result == pool_b
    assert cache == {"https://x.com/a": pool_a, "https://x.com/b": pool_b}
    assert fake.await_count == 1
