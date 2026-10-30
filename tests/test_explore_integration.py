"""Integration tests for `clickcast explore` against the local fixture site.

Unit coverage (queue policy, dedup, budgets, edges) lives in
``test_explore.py`` against stubs. These prove the real loop end-to-end:
a real chromium Session, real discovery, real same-origin go_back restores
— on the multi-page fixture with nav links to Form / Tabs / Privacy / Terms.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clickcast.explore import ExploreConfig, explore_site

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
class TestExploreAgainstFixture:
    async def test_maps_nav_destinations(self, tmp_path: Path, fixture_site_url: str) -> None:
        """The start page's nav links appear as edges to node-per-destination,
        each visited page has a title, and the artifact is a valid v1 payload."""
        out = tmp_path / "map.json"
        payload = await explore_site(
            ExploreConfig(
                url=fixture_site_url,
                out=str(out),
                max_steps=12,
                max_pages=5,
                max_duration=60.0,
                initial_wait=0.2,
            )
        )
        assert payload["schema_version"] == 1
        assert payload["kind"] == "clickcast/explore"
        assert payload["url"] == fixture_site_url

        node_urls = {n["url"]: n for n in payload["graph"]["nodes"]}
        start = node_urls[fixture_site_url]
        assert start["id"] == "n1"
        assert start["title"] == "Clickcast Fixture Site"

        # Every fixture nav destination is a known destination of some edge.
        edge_targets = {e["to"] for e in payload["graph"]["edges"]}
        for leaf in ("form.html", "tabs.html", "privacy.html", "terms.html"):
            dest = node_urls[f"{fixture_site_url}/{leaf}"]
            assert dest["id"] in edge_targets, f"no edge into {leaf}"

        # Visited pages keep their document titles; ids pair pages to nodes.
        for page in payload["pages"]:
            node = node_urls[page["url"]]
            assert page["node"] == node["id"]
            assert node["title"] != ""
            assert page["discovered"], "each visited page pinned discoveries"
            for entry in page["discovered"]:
                assert set(entry) == {"selector", "role", "text", "bbox", "score", "source"}

        # Deterministic identity minus wall time: the serialized payload is
        # JSON-round-trip stable.
        assert json.loads(json.dumps(payload)) == payload
