"""Tests for ``observe()`` (#263) — token-bounded snapshots with stable refs.

The browser-backed tests run against ``tests/fixtures/site/`` via the
session-scoped ``fixture_site_url`` fixture. The token/ref assertions are
deliberately written against the *shipped* serializer
(:func:`clickcast.perception.tokens.compact_json`), not a hand-copied
number, so the budget contract cannot silently drift.
"""

from __future__ import annotations

import math

import pytest

from clickcast import AsyncReel, Reel
from clickcast.core.session import Session
from clickcast.perception.observe import MAX_NODES, SCHEMA, observe
from clickcast.perception.refs import RefCandidate, RefRegistry, registry_for, reset_registry
from clickcast.perception.tokens import (
    DEFAULT_TOKEN_BUDGET,
    MAX_COMPACT_CHARS,
    compact_json,
    estimate_tokens,
)
from tests.mcp.conftest import has_image, live_session, result_json

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Token estimator — pure, no browser
# ---------------------------------------------------------------------------


class TestTokenEstimator:
    def test_estimate_is_ceil_len_over_four(self) -> None:
        assert estimate_tokens("") == 0
        assert estimate_tokens("a") == 1
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcde") == 2
        text = "x" * 3201
        assert estimate_tokens(text) == math.ceil(len(text) / 4)

    def test_compact_json_has_no_whitespace(self) -> None:
        assert compact_json({"a": 1, "b": [1, 2]}) == '{"a":1,"b":[1,2]}'


class TestRefRegistryUnit:
    def test_repeated_role_name_gets_distinct_refs(self) -> None:
        registry = RefRegistry()
        candidates = [
            RefCandidate("link", "Home", (0, 0, 40, 20), 'role=link[name="Home"] >> nth=0'),
            RefCandidate("link", "Home", (0, 700, 40, 20), 'role=link[name="Home"] >> nth=1'),
        ]
        refs = registry.observe(candidates)
        assert refs[0] != refs[1]
        assert registry.lookup(refs[0]) == 'role=link[name="Home"] >> nth=0'

    def test_removed_is_reported_once(self) -> None:
        registry = RefRegistry()
        first = registry.observe(
            [RefCandidate("button", "Go", (10, 10, 40, 20), 'role=button[name="Go"]')]
        )
        ref = first[0]
        registry.observe([])
        assert registry.removed == [ref]
        assert registry.is_removed(ref)
        registry.observe([])
        assert registry.removed == []


# ---------------------------------------------------------------------------
# Fixture site — default observation
# ---------------------------------------------------------------------------


class TestFixtureObservation:
    async def test_default_snapshot_is_within_budget(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/", wait="load")
            snapshot = await observe(session)

        text = compact_json(snapshot)
        assert snapshot["schema"] == SCHEMA
        assert snapshot["viewport"] == [1280, 800]
        assert len(text) <= MAX_COMPACT_CHARS
        assert snapshot["tokens"]["budget"] == DEFAULT_TOKEN_BUDGET
        assert snapshot["tokens"]["estimated"] <= DEFAULT_TOKEN_BUDGET
        assert snapshot["nodes"]
        assert len(snapshot["text_hash"]) == 16
        # Truthful truncation counters.
        truncated = snapshot["truncated"]
        assert len(snapshot["nodes"]) + truncated["omitted"] == truncated["total"]

    async def test_estimated_tokens_is_the_formula_on_the_shipped_bytes(
        self, fixture_site_url: str
    ) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/", wait="load")
            snapshot = await observe(session)

        shipped = compact_json(snapshot)
        assert snapshot["tokens"]["estimated"] == math.ceil(len(shipped) / 4)

    async def test_nodes_are_ranked_and_names_are_truncated(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/", wait="load")
            snapshot = await observe(session)

        assert len(snapshot["nodes"]) <= MAX_NODES
        for node in snapshot["nodes"]:
            assert len(node["name"]) <= 40
            assert node["ref"].startswith("e")
            assert node["state"]["disabled"] is False
            assert len(node["bbox"]) == 4

    async def test_refs_are_identical_across_three_observations(
        self, fixture_site_url: str
    ) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/", wait="load")
            snapshots = [await observe(session) for _ in range(3)]

        assert snapshots[0]["nodes"]
        assert snapshots[0]["nodes"] == snapshots[1]["nodes"] == snapshots[2]["nodes"]
        assert snapshots[1]["removed"] == []
        assert snapshots[2]["removed"] == []

    async def test_adversarial_page_stays_under_budget(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/stress.html", wait="load")
            snapshot = await observe(session)

        text = compact_json(snapshot)
        assert snapshot["truncated"]["total"] >= 200
        assert len(snapshot["nodes"]) <= MAX_NODES
        assert snapshot["tokens"]["estimated"] <= DEFAULT_TOKEN_BUDGET
        assert len(text) <= MAX_COMPACT_CHARS
        assert snapshot["tokens"]["estimated"] == math.ceil(len(text) / 4)
        truncated = snapshot["truncated"]
        assert len(snapshot["nodes"]) + truncated["omitted"] == truncated["total"]
        assert truncated["omitted"] > 150


# ---------------------------------------------------------------------------
# Ref survival and removal
# ---------------------------------------------------------------------------


class TestRefIdentityInPage:
    async def test_nav_ref_survives_opening_a_menu(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/menu.html", wait="load")
            before = await observe(session)
            home_before = next(node for node in before["nodes"] if node["name"] == "Home")

            await session.locator("#menu-toggle").click()
            after = await observe(session)

            home_after = next(node for node in after["nodes"] if node["name"] == "Home")
            assert home_after["ref"] == home_before["ref"]
            # The interaction really happened (menu items are now shipped).
            assert any(node["name"] == "Profile" for node in after["nodes"])

    async def test_detached_element_reports_removed(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/menu.html", wait="load")
            before = await observe(session)
            target = next(node for node in before["nodes"] if node["name"] == "Detach me")
            registry = registry_for(session)
            assert registry.lookup(target["ref"]) == 'role=button[name="Detach me"]'

            await session.evaluate("() => document.querySelector('#detach-me').remove()")
            after = await observe(session)

            assert target["ref"] in after["removed"]
            assert registry.is_removed(target["ref"])
            assert target["ref"] not in {node["ref"] for node in after["nodes"]}

    async def test_hidden_but_present_element_is_not_removed(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/menu.html", wait="load")
            before = await observe(session)
            toggle = next(node for node in before["nodes"] if node["name"] == "Open menu")

            # Hide the toggle without detaching it: it leaves ``nodes`` but is
            # not a removal — the ref survives for when it comes back.
            await session.evaluate(
                "() => { document.querySelector('#menu-toggle').hidden = true; }"
            )
            after = await observe(session)

            assert toggle["ref"] not in {node["ref"] for node in after["nodes"]}
            assert toggle["ref"] not in after["removed"]
            assert not registry_for(session).is_removed(toggle["ref"])

    async def test_reset_registry_starts_a_fresh_namespace(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/", wait="load")
            first = await observe(session)
            reset_registry(session)
            second = await observe(session)

        assert first["nodes"][0]["ref"] == second["nodes"][0]["ref"] == "e1"


# ---------------------------------------------------------------------------
# SDK facade
# ---------------------------------------------------------------------------


class TestReelObserve:
    def test_sync_reel_observe(self, fixture_site_url: str) -> None:
        snapshot = Reel(fixture_site_url + "/", viewport=(1280, 800)).observe()
        assert snapshot["schema"] == SCHEMA
        assert snapshot["tokens"]["estimated"] <= DEFAULT_TOKEN_BUDGET
        assert snapshot["nodes"]

    async def test_async_reel_observe(self, fixture_site_url: str) -> None:
        snapshot = await AsyncReel(fixture_site_url + "/", viewport=(1280, 800)).observe(
            token_budget=400
        )
        assert snapshot["schema"] == SCHEMA
        assert snapshot["tokens"]["budget"] == 400
        assert snapshot["tokens"]["estimated"] <= 400


# ---------------------------------------------------------------------------
# MCP tool — text-only snapshot, in-process client
# ---------------------------------------------------------------------------


class TestMcpObserveTool:
    async def test_observe_returns_text_only_snapshot(self, fixture_site_url: str) -> None:
        async with live_session(
            fixture_site_url=fixture_site_url, goto_home=True, viewport="1280x800"
        ) as client:
            result = await client.call_tool("observe", {"token_budget": 800})

        assert result.isError is not True
        assert has_image(result) is False
        payload = result_json(result)
        assert payload["schema"] == SCHEMA
        assert payload["tokens"]["budget"] == 800
        assert payload["tokens"]["estimated"] <= 800
        assert payload["nodes"]
        assert payload["truncated"]["total"] >= len(payload["nodes"])

    async def test_observe_needs_a_session(self) -> None:
        from tests.mcp.conftest import open_client

        async with open_client() as client:
            result = await client.call_tool("observe", {})
        assert result.isError is True
        assert result_json(result)["error_code"] == "other"
