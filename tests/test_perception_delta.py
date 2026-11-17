"""Tests for structured action deltas (#264) — ``perception/delta.py`` + v5.

Unit tests hand-build :class:`clickcast.perception.delta.Observation` pairs
(no browser). Browser-backed tests run against ``tests/fixtures/site/`` via
the session-scoped ``fixture_site_url`` fixture, and the token assertions are
written against the *shipped* serializer, never a hand-copied number, so the
≤300-token contract cannot drift.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from clickcast.core.session import Session
from clickcast.feedback import Report, StepDelta, StepReport
from clickcast.perception.delta import (
    DELTA_MAX_TOKENS,
    MAX_ERRORS,
    MAX_ITEMS,
    Observation,
    capture,
    diff_observations,
    fit_delta,
)
from clickcast.perception.observe import observe
from clickcast.perception.refs import registry_for
from clickcast.perception.settle import settle
from clickcast.perception.tokens import compact_json, estimate_tokens
from tests.mcp.conftest import has_image, live_session, result_json

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_SITE = REPO_ROOT / "tests" / "fixtures" / "site"


def _node(ref: str, role: str, name: str, bbox: tuple[int, int, int, int]) -> dict:
    return {"ref": ref, "role": role, "name": name, "bbox": list(bbox)}


def _obs(
    nodes: list[dict] | tuple = (),
    texts: list[str] | tuple = (),
    *,
    url: str = "https://x/",
    title: str = "X",
    viewport: tuple[int, int] = (1280, 800),
) -> Observation:
    return Observation(
        url=url,
        title=title,
        viewport=viewport,
        nodes=tuple(nodes),
        texts=tuple(texts),
    )


def _tokens(payload: dict) -> int:
    return estimate_tokens(compact_json(payload))


# ---------------------------------------------------------------------------
# Pure diff behaviour
# ---------------------------------------------------------------------------


class TestDiffUnit:
    def test_identical_observations_produce_no_change_blocks(self) -> None:
        before = _obs([_node("e1", "button", "Go", (10, 10, 80, 30))], ["Hello"])
        delta = diff_observations(before, before)
        assert delta == {"url": "https://x/", "title": "X"}

    def test_added_and_removed_nodes_are_reported(self) -> None:
        before = _obs([_node("e1", "button", "Go", (10, 10, 80, 30))])
        after = _obs([_node("e2", "button", "Stop", (10, 60, 80, 30))])
        delta = diff_observations(before, after)
        assert [node["name"] for node in delta["added"]] == ["Stop"]
        assert [node["name"] for node in delta["removed"]] == ["Go"]

    def test_bbox_jitter_below_threshold_is_not_a_change(self) -> None:
        before = _obs([_node("e1", "button", "Go", (10, 10, 80, 30))])
        after = _obs([_node("e1", "button", "Go", (12, 11, 80, 30))])
        delta = diff_observations(before, after)
        assert "added" not in delta
        assert "removed" not in delta
        assert "layout_shift" not in delta

    def test_jitter_across_a_bucket_boundary_is_not_churn(self) -> None:
        # 30px and 33px fall in different 64px buckets; the stable ref keeps
        # the node's identity, and 3px is under the jitter threshold.
        before = _obs([_node("e1", "button", "Go", (10, 30, 80, 30))])
        after = _obs([_node("e1", "button", "Go", (10, 33, 80, 30))])
        delta = diff_observations(before, after)
        assert "added" not in delta
        assert "removed" not in delta
        assert "layout_shift" not in delta

    def test_movement_beyond_threshold_sets_layout_shift(self) -> None:
        before = _obs([_node("e1", "button", "Go", (10, 10, 80, 30))])
        after = _obs([_node("e1", "button", "Go", (10, 120, 80, 30))])
        delta = diff_observations(before, after)
        assert "added" not in delta
        assert "removed" not in delta
        assert delta["layout_shift"] is True

    def test_remounted_identical_node_does_not_churn(self) -> None:
        before = _obs([_node("e1", "button", "Go", (10, 10, 80, 30))])
        # Same role/name/bbox, brand-new ref — a remount, not a change.
        after = _obs([_node("e42", "button", "Go", (10, 10, 80, 30))])
        delta = diff_observations(before, after)
        assert "added" not in delta
        assert "removed" not in delta

    def test_added_list_is_capped_and_ranked(self) -> None:
        before = _obs([])
        nodes = [
            _node(f"e{i}", "button", f"Item {i}", (10, 500 - i * 10, 80, 20)) for i in range(9)
        ]
        delta = diff_observations(before, _obs(nodes))
        assert len(delta["added"]) == MAX_ITEMS
        # Ranked by y: the topmost entries win.
        assert [node["name"] for node in delta["added"]] == [
            "Item 8",
            "Item 7",
            "Item 6",
            "Item 5",
            "Item 4",
        ]

    def test_console_errors_are_capped_to_the_last_three(self) -> None:
        delta = diff_observations(
            _obs(),
            _obs(),
            console_errors=["e1", "e2", "e3", "e4", "e5"],
        )
        assert delta["console_errors"] == ["e3", "e4", "e5"]
        assert len(delta["console_errors"]) == MAX_ERRORS

    def test_network_failed_is_deduped_and_capped(self) -> None:
        delta = diff_observations(
            _obs(),
            _obs(),
            network_failed=["https://x/a", "https://x/a", "https://x/b", "https://x/c"],
        )
        assert delta["network_failed"] == ["https://x/a", "https://x/b", "https://x/c"]

    def test_text_changes_are_prefixed(self) -> None:
        before = _obs(texts=["Old panel"])
        after = _obs(texts=["New panel"])
        delta = diff_observations(before, after)
        assert delta["text_changes"] == ["+ New panel", "- Old panel"]

    def test_settled_block_is_embedded(self) -> None:
        from clickcast.perception.settle import SettleResult

        settled = SettleResult(ms=812, signals={}, timed_out=())
        delta = diff_observations(_obs(), _obs(), settled=settled)
        assert delta["settled"] == {"ok": True, "ms": 812}


class TestTokenBudget:
    def test_oversized_delta_is_fitted_to_the_shipped_budget(self) -> None:
        before = _obs([], [])
        nodes = [
            _node(f"e{i}", "button", f"very long accessible name number {i}", (10, i * 8, 200, 20))
            for i in range(30)
        ]
        texts = [f"a long body text block that appeared number {i}" * 2 for i in range(30)]
        delta = diff_observations(before, _obs(nodes, texts))
        assert _tokens(delta) <= DELTA_MAX_TOKENS
        assert len(delta["added"]) <= MAX_ITEMS
        assert len(delta["text_changes"]) <= MAX_ITEMS

    def test_fit_delta_always_terminates_and_fits(self) -> None:
        payload = {
            "url": "https://example.com/" + "segment/" * 40,
            "title": "A very long title " * 20,
            "added": [_node(f"e{i}", "x", "y" * 40, (0, 0, 1, 1)) for i in range(5)],
            "text_changes": ["+ " + "z" * 200 for _ in range(5)],
        }
        fitted = fit_delta(payload, max_tokens=40)
        assert _tokens(fitted) <= 40

    def test_model_dump_omits_empty_blocks(self) -> None:
        model = StepDelta.model_validate(diff_observations(_obs(), _obs(), settled=None))
        dumped = model.model_dump(mode="json")
        assert "added" not in dumped
        assert "removed" not in dumped
        assert "text_changes" not in dumped
        assert "layout_shift" not in dumped
        assert "settled" not in dumped
        assert dumped == {"url": "https://x/", "title": "X"}


# ---------------------------------------------------------------------------
# Fixture-site integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFixtureDeltas:
    async def test_tabs_click_reports_the_revealed_panel(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/tabs.html", wait="load")
            before = await capture(session)
            await session.locator('role=tab[name="Details"]').click()
            settled = await settle(session)
            after = await capture(session)

        delta = diff_observations(before, after, settled=settled)
        assert delta["url"] == before.url  # same-page reaction: no navigation
        assert delta["settled"] == {"ok": True, "ms": settled.ms}
        assert any("Details content" in change for change in delta.get("text_changes", [])), delta
        assert _tokens(delta) <= DELTA_MAX_TOKENS

    async def test_every_fixture_page_delta_is_within_budget(self, fixture_site_url: str) -> None:
        pages = sorted(path.name for path in FIXTURE_SITE.glob("*.html"))
        assert pages, "fixture site is empty"
        async with Session(viewport=(1280, 800)) as session:
            for page in pages:
                await session.goto(f"{fixture_site_url}/{page}", wait="load")
                before = await capture(session)
                after = await capture(session)
                delta = diff_observations(before, after, settled=await settle(session))
                shipped = compact_json(StepDelta.model_validate(delta).model_dump(mode="json"))
                assert estimate_tokens(shipped) <= DELTA_MAX_TOKENS, (page, delta)
                assert estimate_tokens(shipped) == math.ceil(len(shipped) / 4)


# ---------------------------------------------------------------------------
# ref=eN resolution
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRefs:
    async def test_ref_resolves_to_the_right_element(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/menu.html", wait="load")
            snapshot = await observe(session)
            toggle = next(node for node in snapshot["nodes"] if node["name"] == "Open menu")
            registry = registry_for(session)

            assert toggle["ref"].startswith("e")
            assert toggle["ref"][1:].isdigit()
            selector = registry.lookup(toggle["ref"])
            assert selector is not None
            assert await session.locator(selector).inner_text() == "Open menu"

    async def test_removed_ref_is_flagged_by_the_registry(self, fixture_site_url: str) -> None:
        async with Session(viewport=(1280, 800)) as session:
            await session.goto(fixture_site_url + "/menu.html", wait="load")
            snapshot = await observe(session)
            target = next(node for node in snapshot["nodes"] if node["name"] == "Detach me")
            registry = registry_for(session)
            await session.evaluate("() => document.querySelector('#detach-me').remove()")
            await observe(session)
            assert registry.is_removed(target["ref"])
            assert registry.lookup(target["ref"]) is not None  # selector still known


# ---------------------------------------------------------------------------
# MCP integration — delta in the tool payload and the sidecar
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpDelta:
    async def test_action_tool_response_carries_delta(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("click", {"selector": "#btn-3d"})
        assert result.isError is not True
        payload = result_json(result)
        assert payload["delta"] is not None
        assert payload["delta"]["url"]
        assert payload["delta"]["settled"]["ok"] is True
        assert has_image(result) is True

    async def test_click_by_ref_reports_the_panel_delta(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            await client.call_tool("goto", {"url": fixture_site_url + "/tabs.html"})
            snapshot = result_json(await client.call_tool("observe", {}))
            details = next(node for node in snapshot["nodes"] if node["name"] == "Details")
            result = await client.call_tool("click", {"ref": details["ref"]})
        assert result.isError is not True
        payload = result_json(result)
        delta = payload["delta"]
        assert delta["url"].endswith("/tabs.html")
        assert any("Details content" in change for change in delta.get("text_changes", []))
        assert delta["settled"]["ok"] is True

    async def test_removed_ref_returns_the_other_error_payload(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            await client.call_tool("goto", {"url": fixture_site_url + "/menu.html"})
            snapshot = result_json(await client.call_tool("observe", {}))
            detach = next(node for node in snapshot["nodes"] if node["name"] == "Detach me")
            # The fixture button removes itself on click, so this action both
            # exercises `ref=` and creates a real removed ref.
            clicked = await client.call_tool("click", {"ref": detach["ref"]})
            await client.call_tool("observe", {})
            result = await client.call_tool("click", {"ref": detach["ref"]})

        assert clicked.isError is not True
        delta = result_json(clicked)["delta"]
        assert [node["name"] for node in delta["removed"]] == ["Detach me"]
        assert result.isError is True
        payload = result_json(result)
        assert payload["error_code"] == "other"
        assert "removed" in payload["error"]

    async def test_unknown_ref_returns_the_other_error_payload(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("click", {"ref": "e999999"})
        assert result.isError is True
        payload = result_json(result)
        assert payload["error_code"] == "other"
        assert "unknown ref" in payload["error"]

    async def test_selector_and_ref_together_is_rejected(self, fixture_site_url: str) -> None:
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            result = await client.call_tool("click", {"selector": "#btn-3d", "ref": "e1"})
        assert result.isError is True
        assert "not both" in result_json(result)["error"]

    async def test_close_session_writes_delta_into_the_sidecar(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        out = tmp_path / "transcript.json"
        async with live_session(fixture_site_url=fixture_site_url, goto_home=True) as client:
            await client.call_tool("click", {"selector": "#btn-3d"})
            result = await client.call_tool("close_session", {"save_transcript": str(out)})
        assert result.isError is not True
        data = json.loads(out.read_text())
        assert data["schema_version"] == 5
        assert data["steps"], "transcript carried no steps"
        # Every action in this live session captured a delta; the click's
        # delta is the one with reaction evidence.
        assert all("delta" in step for step in data["steps"])
        click_step = next(step for step in data["steps"] if step["action"] == "click")
        assert click_step["delta"]["settled"]["ok"] is True
        assert "text_changes" in click_step["delta"] or "added" in click_step["delta"]


# ---------------------------------------------------------------------------
# v1-v4 compatibility — committed example sidecars
# ---------------------------------------------------------------------------

COMMITTED_SIDECARS = [
    REPO_ROOT / "docs" / "demo.gif.json",
    REPO_ROOT / "demo" / "ai-eye-review" / "reel.gif.json",
    REPO_ROOT / "demo" / "live-mcp-session" / "transcript-sidecar.json",
    REPO_ROOT / "demo" / "spatial-grid-overlay" / "reel.gif.json",
    REPO_ROOT / "tests" / "fixtures" / "ci-baseline" / "baseline.json",
]


class TestCommittedSidecars:
    def test_every_committed_sidecar_validates_with_no_delta(self) -> None:
        for path in COMMITTED_SIDECARS:
            assert path.exists(), f"missing committed example sidecar: {path}"
            payload = json.loads(path.read_text())
            report = Report.model_validate(payload)
            assert report.schema_version in {1, 2, 3, 4, 5}, path
            for step in report.steps:
                assert step.delta is None, (path, step.index)

    def test_demo_example_is_the_current_schema(self) -> None:
        payload = json.loads((REPO_ROOT / "docs" / "demo.gif.json").read_text())
        assert payload["schema_version"] == Report.model_fields["schema_version"].default


# ---------------------------------------------------------------------------
# Model-level contract
# ---------------------------------------------------------------------------


class TestStepDeltaModel:
    def test_caps_are_enforced_at_the_model_boundary(self) -> None:
        from pydantic import ValidationError

        node = {"ref": "e1", "role": "button", "name": "Go"}
        with pytest.raises(ValidationError):
            StepDelta(added=[node] * 6)
        with pytest.raises(ValidationError):
            StepDelta(console_errors=["e"] * 4)
        with pytest.raises(ValidationError):
            StepDelta(text_changes=["x"] * 6)

    def test_step_report_defaults_delta_to_none(self) -> None:
        step = StepReport(index=0, action="goto", status="ok", duration_ms=1.0)
        assert step.delta is None

    def test_step_report_round_trips_a_delta(self) -> None:
        step = StepReport(
            index=0,
            action="click",
            status="ok",
            duration_ms=1.0,
            delta=StepDelta(
                url="https://x/",
                title="X",
                text_changes=["+ panel"],
                settled={"ok": True, "ms": 5},  # type: ignore[arg-type]
            ),
        )
        payload = step.model_dump(mode="json")
        assert payload["delta"]["text_changes"] == ["+ panel"]
        assert payload["delta"]["settled"] == {"ok": True, "ms": 5}
        assert "added" not in payload["delta"]
        assert StepReport.model_validate(payload).delta is not None
