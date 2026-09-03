"""Tests for `clickcast.discovery.accessibility` (#196/#197/#198).

Covers:

- `parse_aria_snapshot` — pure parsing of Playwright's ARIA-snapshot YAML
  fragment into (role, name, state), including the graceful-null path for
  elements Playwright can't resolve a name for.
- `capture_accessibility(_batch)` against a real fixture page: a mix of
  well-labeled, unlabeled, disabled, and custom-widget (`aria-expanded`)
  elements — the B1/#197 acceptance criteria — plus a check that
  `discover()`'s own selector/score output is unaffected by running
  accessibility capture alongside it.
- Grid-cell fusion (#198): an element's computed `grid_cell` is
  spot-checked against the pixel boundaries `annotate.grid.draw_grid`
  actually renders for the same pitch, on a real screenshot — not just
  unit-tested against `grid_cell()` in isolation.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from PIL import Image

from clickcast.annotate.grid import GridConfig, draw_grid
from clickcast.core.session import Session
from clickcast.discovery import discover
from clickcast.discovery.accessibility import (
    AccessibilityState,
    AccessibleElement,
    capture_accessibility,
    capture_accessibility_batch,
    parse_aria_snapshot,
)

# ---------------------------------------------------------------------------
# parse_aria_snapshot — pure, no browser
# ---------------------------------------------------------------------------


class TestParseAriaSnapshot:
    def test_labeled_button_with_text_child(self) -> None:
        role, name, state = parse_aria_snapshot('- button "Get started": Go')
        assert role == "button"
        assert name == "Get started"
        assert state.is_empty

    def test_disabled_button(self) -> None:
        role, name, state = parse_aria_snapshot('- button "Disabled Btn" [disabled]')
        assert role == "button"
        assert name == "Disabled Btn"
        assert state.disabled is True
        assert state.checked is None

    def test_unlabeled_role_only(self) -> None:
        # No accessible name at all — Playwright's ARIA snapshot omits the
        # quoted name entirely rather than emitting an empty string.
        role, name, state = parse_aria_snapshot("- button")
        assert role == "button"
        assert name is None
        assert state.is_empty

    def test_empty_snapshot_is_fully_null(self) -> None:
        role, name, state = parse_aria_snapshot("")
        assert role is None
        assert name is None
        assert state.is_empty

    def test_checkbox_checked(self) -> None:
        _, _, state = parse_aria_snapshot('- checkbox "agree" [checked]')
        assert state.checked is True

    def test_checkbox_mixed_is_string_not_bool(self) -> None:
        _, _, state = parse_aria_snapshot('- checkbox "mixed cb" [checked=mixed] [disabled]')
        assert state.checked == "mixed"
        assert state.disabled is True

    def test_custom_widget_aria_expanded(self) -> None:
        role, name, state = parse_aria_snapshot('- button "menu" [expanded]')
        assert role == "button"
        assert name == "menu"
        assert state.expanded is True
        assert state.disabled is None  # not reported → unknown, not False

    def test_pressed_toggle(self) -> None:
        _, _, state = parse_aria_snapshot('- button "toggle" [pressed]')
        assert state.pressed is True

    def test_selected_tab(self) -> None:
        _, _, state = parse_aria_snapshot('- tab "Tab1" [selected]')
        assert state.selected is True

    def test_only_first_line_is_read(self) -> None:
        # A link's nested `/url` child must not leak into the state dict.
        role, name, state = parse_aria_snapshot(
            '- link "Custom Link Name":\n  - /url: "#"\n  - text: link text ignored?'
        )
        assert role == "link"
        assert name == "Custom Link Name"
        assert state.is_empty

    def test_escaped_quote_in_name(self) -> None:
        _, name, _ = parse_aria_snapshot(r'- button "She said \"hi\""')
        assert name == 'She said "hi"'


class TestAccessibilityStateAndElement:
    def test_is_empty_true_for_default(self) -> None:
        assert AccessibilityState().is_empty

    def test_is_empty_false_when_any_field_set(self) -> None:
        assert not AccessibilityState(disabled=True).is_empty

    def test_accessible_element_to_dict_shape(self) -> None:
        el = AccessibleElement(
            selector='role=button[name="X"]',
            bbox=(10, 20, 30, 40),
            score=3,
            role="button",
            name="X",
            state=AccessibilityState(disabled=False),
            grid_cell=(1, 2),
        )
        d = el.to_dict()
        assert d == {
            "selector": 'role=button[name="X"]',
            "bbox": [10, 20, 30, 40],
            "score": 3,
            "role": "button",
            "name": "X",
            "state": {
                "disabled": False,
                "checked": None,
                "expanded": None,
                "pressed": None,
                "selected": None,
            },
            "grid_cell": [1, 2],
        }

    def test_accessible_element_to_dict_null_grid_cell(self) -> None:
        el = AccessibleElement(
            selector="#x",
            bbox=(0, 0, 1, 1),
            score=0,
            role=None,
            name=None,
            state=AccessibilityState(),
        )
        assert el.to_dict()["grid_cell"] is None


# ---------------------------------------------------------------------------
# capture_accessibility(_batch) — real browser, fixture page
# ---------------------------------------------------------------------------

_A11Y_FIXTURE_HTML = """<!DOCTYPE html>
<html><head><title>a11y fixture</title></head>
<body>
  <button id="labeled" aria-label="Get started" style="position:absolute; left:10px; top:10px;">
    Go
  </button>
  <div id="unlabeled" role="button" tabindex="0"
       style="position:absolute; left:10px; top:60px; width:80px; height:24px;">
  </div>
  <button id="disabled-btn" disabled
          style="position:absolute; left:10px; top:110px;">Disabled Btn</button>
  <div id="custom-widget" role="button" aria-expanded="true" aria-label="menu" tabindex="0"
       style="position:absolute; left:10px; top:160px; width:80px; height:24px;">
    menu
  </div>
</body></html>
"""


@pytest_asyncio.fixture
async def a11y_session() -> AsyncIterator[Session]:
    async with Session(viewport=(400, 400)) as sess:
        await sess.page.set_content(_A11Y_FIXTURE_HTML)
        sess.page.set_default_timeout(3000)
        yield sess


@pytest.mark.integration
class TestCaptureAccessibilityIntegration:
    async def test_labeled_button_resolves_role_name(self, a11y_session: Session) -> None:
        acc = await capture_accessibility(
            a11y_session, selector="#labeled", bbox=(10, 10, 60, 20), score=3
        )
        assert acc.role == "button"
        assert acc.name == "Get started"
        assert acc.state.is_empty

    async def test_unlabeled_widget_has_null_role_and_name_or_empty_name(
        self, a11y_session: Session
    ) -> None:
        # An empty `<div role="button">` with no text/aria-label has no
        # accessible name — Playwright's snapshot omits the quoted name,
        # so `parse_aria_snapshot` (and thus this) resolves it to `None`.
        acc = await capture_accessibility(
            a11y_session, selector="#unlabeled", bbox=(10, 60, 80, 24), score=1
        )
        assert acc.role == "button"
        assert acc.name is None
        assert acc.state.is_empty

    async def test_disabled_button_reports_disabled_true(self, a11y_session: Session) -> None:
        acc = await capture_accessibility(
            a11y_session, selector="#disabled-btn", bbox=(10, 110, 90, 20), score=3
        )
        assert acc.role == "button"
        assert acc.state.disabled is True

    async def test_custom_widget_reports_aria_expanded(self, a11y_session: Session) -> None:
        acc = await capture_accessibility(
            a11y_session, selector="#custom-widget", bbox=(10, 160, 80, 24), score=2
        )
        assert acc.role == "button"
        assert acc.name == "menu"
        assert acc.state.expanded is True

    async def test_unresolvable_selector_degrades_gracefully(self, a11y_session: Session) -> None:
        # #197 acceptance: a selector Playwright can't resolve must NOT
        # raise — it degrades to a fully-null role/name/state.
        acc = await capture_accessibility(
            a11y_session,
            selector="#does-not-exist",
            bbox=(0, 0, 1, 1),
            score=0,
            timeout_ms=200,
        )
        assert acc.role is None
        assert acc.name is None
        assert acc.state.is_empty

    async def test_batch_preserves_order_and_count(self, a11y_session: Session) -> None:
        elements = await discover(a11y_session, limit=20)
        assert len(elements) == 4
        batch = await capture_accessibility_batch(a11y_session, elements)
        assert len(batch) == len(elements)
        assert [b.selector for b in batch] == [e.selector for e in elements]

    async def test_capture_does_not_change_discoverys_own_output(
        self, a11y_session: Session
    ) -> None:
        # #197 acceptance: "discovery's existing selector/score output
        # unchanged". Running accessibility capture must not mutate or
        # otherwise affect what a second, independent `discover()` call
        # returns for the same page.
        before = await discover(a11y_session, limit=20)
        await capture_accessibility_batch(a11y_session, before)
        after = await discover(a11y_session, limit=20)
        assert [(e.selector, e.role, e.text, e.bbox, e.score) for e in before] == [
            (e.selector, e.role, e.text, e.bbox, e.score) for e in after
        ]

    async def test_no_grid_leaves_grid_cell_null(self, a11y_session: Session) -> None:
        acc = await capture_accessibility(
            a11y_session, selector="#labeled", bbox=(10, 10, 60, 20), score=3, grid=None
        )
        assert acc.grid_cell is None

    async def test_disabled_grid_config_leaves_grid_cell_null(self, a11y_session: Session) -> None:
        acc = await capture_accessibility(
            a11y_session,
            selector="#labeled",
            bbox=(10, 10, 60, 20),
            score=3,
            grid=GridConfig(enabled=False, pitch=100),
        )
        assert acc.grid_cell is None


# ---------------------------------------------------------------------------
# Grid-cell fusion (#198) — spot-checked against a rendered overlay image
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGridCellFusionAgainstRenderedOverlay:
    async def test_grid_cell_matches_rendered_gridline_boundaries(
        self, a11y_session: Session
    ) -> None:
        """The computed grid_cell must agree with where a human reading the
        actual rendered ``--grid`` overlay image would place the element:
        the element's bbox falls between the major gridlines at
        ``col*pitch`` and ``(col+1)*pitch`` (and same for rows) — verified
        by sampling the rendered image for lit gridline pixels at those
        exact boundaries, not just re-deriving the same division in
        isolation.
        """
        pitch = 100
        grid = GridConfig(enabled=True, pitch=pitch, style="full", color="#FFFFFFFF")

        png_bytes = await a11y_session.screenshot()
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        gridded = draw_grid(img.copy(), grid)

        elements = await discover(a11y_session, limit=20)
        batch = await capture_accessibility_batch(a11y_session, elements, grid=grid)
        assert batch

        for el, acc in zip(elements, batch, strict=True):
            assert acc.grid_cell is not None
            col, row = acc.grid_cell
            x, y, _w, _h = el.bbox
            # The element's own top-left pixel must lie within the cell
            # `grid_cell` claims — i.e. between the major boundaries.
            assert col * pitch <= x < (col + 1) * pitch
            assert row * pitch <= y < (row + 1) * pitch
            # And the major gridline that BOUNDS this cell on the left/top
            # (when not the 0th cell) must actually be a lit pixel in the
            # rendered overlay — the same line a human would read the
            # element's position off of.
            if col > 0:
                boundary_x = col * pitch
                assert gridded.getpixel((boundary_x, y))[3] > 0, (
                    f"expected a lit major gridline pixel at x={boundary_x}"
                )
            if row > 0:
                boundary_y = row * pitch
                assert gridded.getpixel((x, boundary_y))[3] > 0, (
                    f"expected a lit major gridline pixel at y={boundary_y}"
                )
