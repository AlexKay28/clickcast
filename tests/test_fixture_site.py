"""End-to-end tests exercising the local fixture site.

These prove the fixture is wired correctly and give the pipeline a realistic
target that goes beyond ``data:`` URLs — real navigation, form submission,
ARIA tab widgets. They are marked ``integration`` (chromium required).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from clickcast import Reel, discover
from clickcast.core.actions import (
    ClickStep,
    SelectStep,
    TypeStep,
    execute,
)
from clickcast.core.session import Session
from clickcast.discovery import discover as async_discover

# ------------------------------------------------------------------
# Sanity — is the fixture actually running?
# ------------------------------------------------------------------


class TestFixtureIsUp:
    def test_server_returns_index(self, fixture_site_url: str) -> None:
        import urllib.request

        with urllib.request.urlopen(fixture_site_url, timeout=2) as resp:
            body = resp.read().decode()
        assert "<title>Clickcast Fixture Site" in body
        assert "3D" in body

    def test_server_is_on_loopback(self, fixture_site_url: str) -> None:
        # No accidental external listening — a nice safety net.
        assert fixture_site_url.startswith("http://127.0.0.1:")

    def test_server_port_is_actually_open(self, fixture_site_url: str) -> None:
        host, _, port_s = fixture_site_url.removeprefix("http://").partition(":")
        with socket.create_connection((host, int(port_s)), timeout=0.5):
            pass


# ------------------------------------------------------------------
# Discovery — the site is a realistic target for the ranking heuristics
# ------------------------------------------------------------------


@pytest.mark.integration
class TestDiscoveryAgainstFixture:
    async def test_primary_buttons_all_discovered(self, fixture_site_url: str) -> None:
        # Nav links (Home / Form / Tabs) tie with primary buttons on the ranking
        # heuristic and win the y-then-x tie-break by being higher on the page,
        # so we can't assert "top N". Instead, verify all 3 primary buttons are
        # in the discovered set, and that "Compare countries" (the aria-label)
        # wins over the visible text "Compare".
        async with Session(viewport=(800, 600)) as sess:
            await sess.goto(fixture_site_url, wait="load")
            elements = await async_discover(sess, limit=20)
        texts = {e.text for e in elements}
        assert "3D" in texts
        assert "Compare countries" in texts
        assert "Reset" in texts


@pytest.mark.integration
class TestDiscoveryTopLevelSync:
    def test_top_level_discover_finds_expected_elements(self, fixture_site_url: str) -> None:
        elements = discover(fixture_site_url, viewport=(800, 600), limit=10)
        button_texts = {e.text for e in elements if e.role == "button"}
        assert {"3D", "Compare countries", "Reset"} <= button_texts


# ------------------------------------------------------------------
# Actions against a real multi-page site
# ------------------------------------------------------------------


@pytest.mark.integration
class TestActionsAgainstFixture:
    async def test_click_updates_marker(self, fixture_site_url: str) -> None:
        async with Session(viewport=(800, 600)) as sess:
            await sess.goto(fixture_site_url, wait="load")
            result = await execute(ClickStep(selector="#btn-3d"), sess)
            assert result.ok
            marker = await sess.page.locator("#clicked-name").text_content()
            assert marker == "3D"

    async def test_form_type_select_submit(self, fixture_site_url: str) -> None:
        async with Session(viewport=(800, 600)) as sess:
            await sess.goto(f"{fixture_site_url}/form.html", wait="load")
            await execute(TypeStep(into="#name", text="Alice"), sess)
            await execute(SelectStep(into="#country", value="fr"), sess)
            r = await execute(ClickStep(selector="#submit-btn"), sess)
            assert r.ok
            result_text = await sess.page.locator("#result").text_content()
            assert result_text is not None
            assert "Alice" in result_text
            assert "fr" in result_text

    async def test_tab_widget_switches_panels(self, fixture_site_url: str) -> None:
        async with Session(viewport=(800, 600)) as sess:
            await sess.goto(f"{fixture_site_url}/tabs.html", wait="load")
            # Overview panel starts visible, Details hidden
            assert await sess.page.locator("#panel-overview").is_visible()
            assert not await sess.page.locator("#panel-details").is_visible()
            r = await execute(ClickStep(selector="#tab-details"), sess)
            assert r.ok
            assert await sess.page.locator("#panel-details").is_visible()
            assert not await sess.page.locator("#panel-overview").is_visible()


# ------------------------------------------------------------------
# End-to-end via the public Python API
# ------------------------------------------------------------------


@pytest.mark.integration
class TestReelAgainstFixture:
    def test_reel_save_produces_gif_and_valid_sidecar(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        out = tmp_path / "tour.gif"
        result_path = (
            Reel(fixture_site_url, viewport=(600, 400), fps=4, dwell=0.25)
            .goto(wait="load")
            .click("#btn-3d", label="Click 3D", dwell=0.25)
            .save(out)
        )
        assert result_path == out
        sidecar = out.with_suffix(out.suffix + ".json")
        assert sidecar.exists()
        payload = json.loads(sidecar.read_text())
        # This is the exact contract downstream AI reads:
        assert payload["schema_version"] == 1
        assert payload["media"]["format"] == "gif"
        # Two steps ran (goto + click)
        assert len(payload["steps"]) == 2
        actions = [s["action"] for s in payload["steps"]]
        assert actions == ["goto", "click"]
        # Post-goto page_state captures the fixture's title
        page_state = payload["steps"][0].get("page_state")
        assert page_state is not None
        assert "Clickcast Fixture Site" in page_state["title"]
