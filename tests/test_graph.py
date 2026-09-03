"""Tests for :mod:`clickcast.feedback.graph` — #107 Track C, v2 additive."""

from __future__ import annotations

import json
from pathlib import Path

from clickcast.feedback import (
    Edge,
    Graph,
    Media,
    PageNode,
    PageState,
    Report,
    StepReport,
    build_graph,
    dom_signature,
    load,
    write,
)


def _step(index: int, action: str = "click", *, url: str = "", **args: object) -> StepReport:
    """Build a StepReport with a synthesized ``page_state.url_after``."""
    return StepReport(
        index=index,
        action=action,
        args={"selector": args.get("selector", "")} if "selector" in args else {},
        status="ok",
        duration_ms=100.0,
        page_state=PageState(url_after=url, title=args.get("title", "") or ""),
    )


# ---------------------------------------------------------------------------
# build_graph — core cases
# ---------------------------------------------------------------------------


class TestBuildGraph:
    def test_empty_steps_returns_none(self) -> None:
        assert build_graph([]) is None

    def test_no_page_states_returns_none(self) -> None:
        # A tour with steps but no page_state (e.g. only pre-navigation
        # actions) should return None — nothing to graph.
        steps = [
            StepReport(index=0, action="wait", status="ok", duration_ms=50.0),
            StepReport(index=1, action="wait", status="ok", duration_ms=50.0),
        ]
        assert build_graph(steps) is None

    def test_three_page_tour_produces_expected_shape(self) -> None:
        steps = [
            _step(0, "goto", url="https://example.com/a", title="A"),
            _step(1, "click", url="https://example.com/b", selector="a#to-b"),
            _step(2, "click", url="https://example.com/c", selector="a#to-c"),
        ]
        graph = build_graph(steps)
        assert graph is not None
        pages = [n for n in graph.nodes if isinstance(n, PageNode)]
        assert len(pages) == 3
        assert [n.url for n in pages] == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]
        assert len(graph.edges) == 2
        assert all(e.transition_kind == "navigation" for e in graph.edges)
        # Edge selectors round-trip from step args.
        assert graph.edges[0].selector == "a#to-b"
        assert graph.edges[1].selector == "a#to-c"

    def test_all_same_url_produces_single_node_zero_edges(self) -> None:
        steps = [_step(i, url="https://example.com/x") for i in range(5)]
        graph = build_graph(steps)
        assert graph is not None
        assert len(graph.nodes) == 1
        assert graph.edges == []
        page = graph.nodes[0]
        assert isinstance(page, PageNode)
        assert page.first_seen_step == 0
        assert page.last_seen_step == 4

    def test_component_nodes_empty_in_v2_first_slice(self) -> None:
        # v2-first ships component-node extraction empty — the
        # landmark-detection pass is a deferred follow-up. The graph
        # should still validate and carry only page nodes.
        graph = build_graph([_step(0, url="https://example.com/a")])
        assert graph is not None
        assert not any(getattr(n, "kind", "") == "component" for n in graph.nodes)

    def test_revisit_updates_last_seen_step(self) -> None:
        steps = [
            _step(0, url="https://example.com/a"),
            _step(1, url="https://example.com/b"),
            _step(2, url="https://example.com/a"),
        ]
        graph = build_graph(steps)
        assert graph is not None
        by_url = {n.url: n for n in graph.nodes if isinstance(n, PageNode)}
        assert by_url["https://example.com/a"].first_seen_step == 0
        assert by_url["https://example.com/a"].last_seen_step == 2
        # Two edges: a->b and b->a.
        assert len(graph.edges) == 2

    def test_deterministic_across_runs(self) -> None:
        steps = [
            _step(0, url="https://example.com/a"),
            _step(1, url="https://example.com/b"),
        ]
        first = build_graph(steps)
        second = build_graph(steps)
        assert first is not None and second is not None
        assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# dom_signature helper — dedup fingerprint for the deferred landmark pass
# ---------------------------------------------------------------------------


class TestDomSignature:
    def test_same_input_same_hex(self) -> None:
        a = dom_signature("navigation", "Primary", (0, 0, 1280, 64))
        b = dom_signature("navigation", "Primary", (0, 0, 1280, 64))
        assert a == b
        assert len(a) == 16

    def test_role_differences_produce_different_sigs(self) -> None:
        a = dom_signature("navigation", "Primary", (0, 0, 1280, 64))
        b = dom_signature("contentinfo", "Primary", (0, 0, 1280, 64))
        assert a != b

    def test_bbox_bucket_absorbs_small_shifts(self) -> None:
        # A ±30px shift stays inside the 64px bucket → same signature.
        # Same-position-across-pages nav dedupes cleanly.
        a = dom_signature("navigation", "Primary", (0, 0, 1280, 64))
        b = dom_signature("navigation", "Primary", (10, 5, 1280, 64))
        assert a == b

    def test_bbox_bucket_distinguishes_relocated_element(self) -> None:
        # A genuinely relocated sidebar (moved from left to right of
        # a 1280px viewport) crosses many buckets → distinct signature.
        a = dom_signature("complementary", "Sidebar", (0, 100, 240, 600))
        b = dom_signature("complementary", "Sidebar", (1040, 100, 240, 600))
        assert a != b


# ---------------------------------------------------------------------------
# Backward compatibility — v1 sidecars round-trip through the v2 model
# ---------------------------------------------------------------------------


class TestV1Backcompat:
    def _v1_payload(self) -> dict[str, object]:
        # Handcrafted v1 sidecar shape — no ``graph`` field, schema_version=1.
        return {
            "schema_version": 1,
            "clickcast_version": "0.2.1",
            "url": "https://example.com",
            "engine": "chromium",
            "viewport": [1280, 800],
            "started_at": "2026-07-23T15:00:00+00:00",
            "duration_s": 1.5,
            "media": {
                "path": "tour.gif",
                "format": "gif",
                "size_bytes": 1024,
                "frame_count": 12,
                "duration_s": 1.0,
                "fps": 12,
            },
            "discovered_elements": [],
            "steps": [
                {
                    "index": 0,
                    "action": "goto",
                    "args": {"url": "https://example.com"},
                    "status": "ok",
                    "duration_ms": 100.0,
                    "frames": [],
                }
            ],
            "warnings": [],
            "errors": [],
        }

    def test_v1_payload_validates_under_v2_model(self) -> None:
        report = Report.model_validate(self._v1_payload())
        assert report.schema_version == 1
        assert report.graph is None

    def test_v1_file_round_trips_through_load(self, tmp_path: Path) -> None:
        path = tmp_path / "v1.gif.json"
        path.write_text(json.dumps(self._v1_payload()))
        report = load(path)
        assert report.schema_version == 1
        assert report.graph is None
        assert report.steps[0].action == "goto"

    def test_edge_serializes_from_alias_not_field_name(self) -> None:
        # The pydantic field is ``from_`` (``from`` is a reserved keyword),
        # but the JSON contract is ``"from"``. Round-trip both directions.
        edge = Edge.model_validate({"from": "n1", "to": "n2", "via_step": 3})
        dumped = edge.model_dump(mode="json")
        assert "from" in dumped
        assert "from_" not in dumped
        # And it round-trips.
        assert Edge.model_validate(dumped).from_ == "n1"

    def test_v2_write_then_load_preserves_graph(self, tmp_path: Path) -> None:
        report = Report(
            clickcast_version="0.2.4",
            started_at="2026-07-30T00:00:00+00:00",
            duration_s=1.0,
            media=Media(
                path="tour.gif",
                format="gif",
                size_bytes=100,
                frame_count=1,
                duration_s=1.0,
                fps=1,
            ),
            graph=Graph(
                nodes=[
                    PageNode(
                        id="n1",
                        url="https://example.com/a",
                        first_seen_step=0,
                        last_seen_step=0,
                    ),
                    PageNode(
                        id="n2",
                        url="https://example.com/b",
                        first_seen_step=1,
                        last_seen_step=1,
                    ),
                ],
                edges=[Edge(from_="n1", to="n2", via_step=1)],
            ),
        )
        path = write(report, tmp_path / "v2.gif.json")
        loaded = load(path)
        # Shipped default bumped to v4 in #196 (elements[].accessibility).
        # v2 fixtures with an explicit ``schema_version: 2`` still
        # round-trip; this fixture constructs a fresh Report so it picks
        # up the current default.
        assert loaded.schema_version == 4
        assert loaded.graph is not None
        assert len(loaded.graph.nodes) == 2
        assert len(loaded.graph.edges) == 1
        assert loaded.graph.edges[0].from_ == "n1"


# ---------------------------------------------------------------------------
# Wire — ReportBuilder.build attaches a graph best-effort
# ---------------------------------------------------------------------------


class TestBuilderWire:
    def test_builder_attaches_graph_when_pages_present(self) -> None:
        from clickcast.feedback import ReportBuilder

        builder = ReportBuilder(url="https://example.com")
        # Directly append synthesized steps into the builder's private list
        # to sidestep the browser dependency. This is the shape the
        # collector would produce.
        builder._steps.extend(  # type: ignore[attr-defined]
            [
                _step(0, url="https://example.com/a"),
                _step(1, url="https://example.com/b"),
            ]
        )
        report = builder.build(
            Media(
                path="tour.gif",
                format="gif",
                size_bytes=100,
                frame_count=1,
                duration_s=1.0,
                fps=1,
            )
        )
        assert report.graph is not None
        assert len(report.graph.nodes) == 2
        assert len(report.graph.edges) == 1

    def test_builder_produces_no_graph_for_empty_tour(self) -> None:
        from clickcast.feedback import ReportBuilder

        builder = ReportBuilder()
        report = builder.build(
            Media(
                path="tour.gif",
                format="gif",
                size_bytes=100,
                frame_count=1,
                duration_s=1.0,
                fps=1,
            )
        )
        assert report.graph is None
