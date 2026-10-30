"""Explore engine — state-graph site exploration without a reel.

Mirrors ``test_cli_auto_traversal.py``'s stub pattern: patch
``clickcast.explore.execute`` / ``discover`` / ``Session`` so the engine
runs without a browser, and inspect the emitted payload. What's under test
is the *policy* — queue order, dedup, budgets, edge recording — not the
browser (that's the integration suite's job).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import typer

from clickcast.explore import ExploreConfig, explore_site
from tests._stubs import FakeSession, make_element, make_result


def _stub_session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    fake_sess = FakeSession()

    class _SessCtor:
        def __init__(self, **_kwargs: Any) -> None:
            self._sess = fake_sess

        async def __aenter__(self) -> FakeSession:
            return self._sess

        async def __aexit__(self, *args: Any) -> None:
            return None

    monkeypatch.setattr("clickcast.explore.Session", _SessCtor)
    return fake_sess


def explore_cfg(**overrides: Any) -> ExploreConfig:
    defaults: dict[str, Any] = {"url": "https://x.com/", "max_duration": 10.0}
    defaults.update(overrides)
    return ExploreConfig(**defaults)


class TestQueueOrderingAndNodes:
    @pytest.mark.asyncio
    async def test_bfs_visits_breadth_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Start page discovers 3 nav destinations; BFS visits each of them
        (page order 1..4) before anything deeper — and node ids follow
        first-seen order."""
        fake_sess = _stub_session(monkeypatch)
        counters: dict[str, int] = {}

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                here = fake_sess.page.url
                n = counters.get(here, 0) + 1
                counters[here] = n
                if here == "https://x.com/" and n <= 3:
                    fake_sess.page.url = f"https://x.com/dest-{n}"
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(3)]),
            ),
        ):
            payload = await explore_site(explore_cfg(max_pages=10, max_steps=12))

        visited = [p["url"] for p in payload["pages"]]
        assert visited == [
            "https://x.com/",
            "https://x.com/dest-1",
            "https://x.com/dest-2",
            "https://x.com/dest-3",
        ]
        assert [n["id"] for n in payload["graph"]["nodes"]] == ["n1", "n2", "n3", "n4"]
        assert payload["graph"]["nodes"][0]["url"] == "https://x.com/"

    @pytest.mark.asyncio
    async def test_dfs_visits_deepest_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With traversal='dfs', the most recently discovered destination is
        visited next — depth-first, mirroring the auto engine's queue policy."""
        fake_sess = _stub_session(monkeypatch)
        counters: dict[str, int] = {}

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
                return make_result()
            if cls == "ClickStep":
                here = fake_sess.page.url
                n = counters.get(here, 0) + 1
                counters[here] = n
                if here in ("https://x.com/", "https://x.com/dest-3") and n == 1:
                    fake_sess.page.url = (
                        "https://x.com/dest-3" if here == "https://x.com/" else "https://x.com/deep"
                    )
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element("L")]),
            ),
        ):
            payload = await explore_site(explore_cfg(traversal="dfs", max_pages=10, max_steps=6))

        assert [p["url"] for p in payload["pages"]][:3] == [
            "https://x.com/",
            "https://x.com/dest-3",
            "https://x.com/deep",
        ]


class TestDedupAndBudget:
    @pytest.mark.asyncio
    async def test_fragment_urls_dedupe_to_one_visit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """/a#foo and /a#bar are the same page — explore visits it once."""
        fake_sess = _stub_session(monkeypatch)
        clicks = {"n": 0}

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep":
                clicks["n"] += 1
                if fake_sess.page.url == "https://x.com/":
                    fake_sess.page.url = (
                        "https://x.com/a#foo" if clicks["n"] == 1 else "https://x.com/a#bar"
                    )
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element("L"), make_element("M")]),
            ),
        ):
            payload = await explore_site(explore_cfg(max_pages=10, max_steps=8))

        assert [p["url"] for p in payload["pages"]] == ["https://x.com/", "https://x.com/a#foo"]
        # Both clicks DID fire on page 1 (they're separate discoveries).
        assert payload["pages"][0]["clicks_ok"] == 2

    @pytest.mark.asyncio
    async def test_click_budget_caps_exploration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """max_steps=1 means one successful click: after it fires, the engine
        stops even though the queue holds more destinations."""
        fake_sess = _stub_session(monkeypatch)

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep" and fake_sess.page.url == "https://x.com/":
                fake_sess.page.url = "https://x.com/dest"
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(3)]),
            ),
        ):
            payload = await explore_site(explore_cfg(max_steps=1, max_pages=10))

        assert [p["url"] for p in payload["pages"]] == ["https://x.com/"]
        # The queued destination is known but never visited — still a node.
        assert any(n["url"] == "https://x.com/dest" for n in payload["graph"]["nodes"])

    @pytest.mark.asyncio
    async def test_cross_origin_destinations_are_recorded_but_not_driven(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A click that lands on another origin still yields a node + edge,
        but the engine neither enqueues it for a visit nor keeps clicking."""
        fake_sess = _stub_session(monkeypatch)

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep" and fake_sess.page.url == "https://x.com/":
                fake_sess.page.url = "https://elsewhere.org/pricing"
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(3)]),
            ),
        ):
            payload = await explore_site(explore_cfg(max_pages=10, max_steps=8))

        urls = [p["url"] for p in payload["pages"]]
        assert urls == ["https://x.com/"]  # foreign origin never visited
        node_urls = {n["url"] for n in payload["graph"]["nodes"]}
        assert "https://elsewhere.org/pricing" in node_urls
        # And the engine didn't drive the foreign page: only the start page
        # has discoveries, and it stopped clicking after the bail.
        assert payload["pages"][0]["clicks_ok"] == 1


class TestEdges:
    @pytest.mark.asyncio
    async def test_edges_carry_selector_and_navigation_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each URL-changing click yields one edge with the endpooint node ids,
        the click's selector, and transition_kind='navigation'."""
        fake_sess = _stub_session(monkeypatch)

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif (
                cls == "ClickStep"
                and step.selector == 'text="About"'
                and fake_sess.page.url == "https://x.com/"
            ):
                fake_sess.page.url = "https://x.com/about"
            return make_result()

        elements = [make_element("About"), make_element("No-op")]
        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch("clickcast.explore.discover", AsyncMock(return_value=elements)),
        ):
            payload = await explore_site(explore_cfg(max_steps=5, max_pages=2))

        assert payload["graph"]["edges"] == [
            {
                "from": "n1",
                "to": "n2",
                "via_step": 0,
                "selector": 'text="About"',
                "transition_kind": "navigation",
            }
        ]

    @pytest.mark.asyncio
    async def test_consecutive_failures_bail_the_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three failed clicks in a row stop the page (DOM-drift guard,
        same policy as the auto engine)."""
        _stub_session(monkeypatch)

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            if step.__class__.__name__ == "GotoStep":
                return make_result()
            return make_result(ok=False)

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(10)]),
            ),
        ):
            payload = await explore_site(explore_cfg(max_steps=50, max_pages=10))

        assert payload["pages"][0]["clicks_failed"] == 3
        assert len(payload["pages"]) == 1


class TestPayloadContract:
    @pytest.mark.asyncio
    async def test_payload_shape_is_stable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ "kind: clickcast/explore", schema_version 1, clickcast_version set,
        per-page discoveries clipped to per_page_discoveries."""
        fake_sess = _stub_session(monkeypatch)

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            if step.__class__.__name__ == "GotoStep":
                fake_sess.page.url = step.url
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(20)]),
            ),
        ):
            payload = await explore_site(
                explore_cfg(max_steps=5, max_pages=1, per_page_discoveries=3)
            )

        assert payload["schema_version"] == 1
        assert payload["kind"] == "clickcast/explore"
        assert payload["url"] == "https://x.com/"
        assert payload["traversal"] == "bfs"
        assert isinstance(payload["wall_s"], float)
        assert len(payload["pages"][0]["discovered"]) == 3
        assert payload["pages"][0]["title"] == "title of https://x.com/"
        assert set(payload["pages"][0]["discovered"][0]) == {
            "selector",
            "role",
            "text",
            "bbox",
            "score",
            "source",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cfg_override",
        [
            {"max_pages": 0},
            {"max_duration": 0.0},
            {"traversal": "wander"},
        ],
    )
    async def test_validation_dies_cleanly(
        self, monkeypatch: pytest.MonkeyPatch, cfg_override: dict[str, Any]
    ) -> None:
        _stub_session(monkeypatch)  # Session must not even be constructed
        with pytest.raises(typer.Exit):
            await explore_site(explore_cfg(**cfg_override))


class TestGoBackRestore:
    @pytest.mark.asyncio
    async def test_same_origin_nav_restores_page_before_next_click(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a same-origin navigation the engine go_back()s so the
        remaining discovered elements still land on the original page."""
        fake_sess = _stub_session(monkeypatch)
        clicks: list[str] = []

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep":
                # Record WHERE each click landed.
                clicks.append(fake_sess.page.url)
                if step.selector == 'text="About"' and fake_sess.page.url == "https://x.com/":
                    fake_sess.page.url = "https://x.com/about"
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element("About"), make_element("Docs")]),
            ),
        ):
            await explore_site(explore_cfg(max_steps=5, max_pages=2))

        assert fake_sess.page.go_back_history == ["https://x.com/about"]
        assert fake_sess.page.go_back_kwargs[0]["wait_until"] == "domcontentloaded"
        # Both start-page clicks ran on the restored page — go_back landed
        # the engine back on "https://x.com/" before the second element fired
        # (the go_back itself, not the queue, is what's under test: after it,
        # url_before was x.com again).
        assert clicks[:2] == ["https://x.com/", "https://x.com/"]

    @pytest.mark.asyncio
    async def test_go_back_failure_stops_the_page_not_the_explore(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A same-origin nav whose go_back() raises ends the PAGE (remaining
        discoveries are dropped) but the exploration continues if the queue
        holds more URLs."""

        async def _raise(**_kw: Any) -> None:
            raise RuntimeError("navigation interrupted")

        fake_sess = _stub_session(monkeypatch)
        fake_sess.page.go_back = _raise  # type: ignore[method-assign]

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep" and fake_sess.page.url == "https://x.com/":
                fake_sess.page.url = "https://x.com/about"
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(5)]),
            ),
        ):
            payload = await explore_site(explore_cfg(max_steps=10, max_pages=3))

        # Page 1's nav DID register an edge + enqueued destination, then the
        # broken go_back cut the page short at exactly one click.
        assert payload["pages"][0]["clicks_ok"] == 1
        assert any(
            e["to"] == "n2" and e["selector"] == 'text="L0"' for e in payload["graph"]["edges"]
        )
        # ...and the enqueued /about page was still explored next.
        assert payload["pages"][1]["url"] == "https://x.com/about"

    @pytest.mark.asyncio
    async def test_go_back_landing_mismatch_stops_the_page(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """go_back that lands somewhere ELSE than we started means the
        browser state is no longer the page we were exploring — stop the
        page rather than clicking on a stranger."""
        fake_sess = _stub_session(monkeypatch)

        async def _decoy_go_back(**_kw: Any) -> None:
            # Pop the nav destination and push a decoy — the engine should
            # notice it isn't where it expected and keep nothing further
            # from this page's pool.
            fake_sess.page.url = "https://x.com/somewhere-else"

        fake_sess.page.go_back = _decoy_go_back  # type: ignore[method-assign]

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                fake_sess.page.url = step.url
            elif cls == "ClickStep" and fake_sess.page.url == "https://x.com/":
                fake_sess.page.url = "https://x.com/about"
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element(f"L{i}") for i in range(5)]),
            ),
        ):
            payload = await explore_site(explore_cfg(max_steps=10, max_pages=1))

        assert payload["pages"][0]["clicks_ok"] == 1


class TestPageFailurePaths:
    @pytest.mark.asyncio
    async def test_goto_failure_skips_page_and_continues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreachable URL is skipped (still a payload page with no
        discoveries); a queued sibling still gets explored."""
        fake_sess = _stub_session(monkeypatch)
        gotos: list[str] = []

        async def _fake_execute(step: Any, _sess: Any, **_kw: Any) -> Any:
            cls = step.__class__.__name__
            if cls == "GotoStep":
                gotos.append(step.url)
                if "dead" in step.url:
                    return make_result(ok=False)
                fake_sess.page.url = step.url
            elif cls == "ClickStep" and fake_sess.page.url == "https://x.com/":
                fake_sess.page.url = "https://x.com/dead"
            return make_result()

        with (
            patch("clickcast.explore.execute", side_effect=_fake_execute),
            patch(
                "clickcast.explore.discover",
                AsyncMock(return_value=[make_element("Dead link"), make_element("Safe")]),
            ),
        ):
            payload = await explore_site(explore_cfg(max_steps=10, max_pages=5))

        # The dead page was visited-but-failed: no discoveries, in pages[].
        dead = next(p for p in payload["pages"] if "dead" in p["url"])
        assert dead["discovered"] == []
        assert dead["clicks_ok"] == 0
        # And it produced a node + edge (it IS part of the app's shape —
        # agents want broken links in the map).
        assert any(n["url"] == "https://x.com/dead" for n in payload["graph"]["nodes"])
        assert any(e["to"] == "n2" for e in payload["graph"]["edges"])
        # Exploration continued: the safe element still fired after the
        # (fake-restoring) go_back.
        assert gotos.count("https://x.com/dead") == 1
