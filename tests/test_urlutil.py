"""Tests for `clickcast.discovery.urlutil`."""

from __future__ import annotations

import pytest

from clickcast.discovery.urlutil import is_same_origin, normalize_url


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://x.com/a#top", "https://x.com/a"),
            ("https://x.com/a#", "https://x.com/a"),
            ("https://x.com/a", "https://x.com/a"),
            ("https://x.com/a?q=1", "https://x.com/a?q=1"),
            ("https://x.com/a?q=1#foo", "https://x.com/a?q=1"),
            ("https://x.com/", "https://x.com/"),
        ],
    )
    def test_strips_fragment_preserves_query(self, raw: str, expected: str) -> None:
        assert normalize_url(raw) == expected

    def test_distinct_paths_kept_distinct(self) -> None:
        assert normalize_url("https://x.com/a") != normalize_url("https://x.com/b")

    def test_distinct_query_kept_distinct(self) -> None:
        # Different query strings mean different pages for tour purposes.
        assert normalize_url("https://x.com/s?q=1") != normalize_url("https://x.com/s?q=2")


class TestIsSameOrigin:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("https://x.com/a", "https://x.com/b"),
            ("https://x.com/", "https://x.com/deep/path?q=1"),
            ("http://localhost:3000/", "http://localhost:3000/x"),
        ],
    )
    def test_true_for_same_origin(self, a: str, b: str) -> None:
        assert is_same_origin(a, b)

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("https://x.com", "http://x.com"),  # scheme differs
            ("https://x.com", "https://y.com"),  # host differs
            ("https://x.com", "https://sub.x.com"),  # host differs (subdomain)
            ("http://x.com:3000", "http://x.com:4000"),  # port differs
            ("http://x.com", "http://x.com:8080"),  # implicit vs explicit port
        ],
    )
    def test_false_when_origin_differs(self, a: str, b: str) -> None:
        assert not is_same_origin(a, b)
