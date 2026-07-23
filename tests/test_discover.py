from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from clickcast.core.session import Session
from clickcast.discover import Element, discover
from clickcast.discover.discover import _dedup, _pick_selector, _score


class TestScore:
    def _base(self, **overrides: object) -> dict[str, object]:
        c = {
            "role": "button",
            "name": "Click me",
            "bbox": [100, 100, 60, 30],
            "visible": True,
            "inViewport": True,
            "inFooter": False,
            "ariaHidden": False,
            "skipClass": False,
        }
        c.update(overrides)
        return c

    def test_visible_in_viewport_named_scores_3(self) -> None:
        # +2 visible+in-viewport, +1 name length in [2, 30]
        assert _score(self._base()) == 3

    def test_tiny_bbox_penalizes(self) -> None:
        # 20x20 = 400 < 24*24 = 576 → -2
        c = self._base(bbox=[0, 0, 20, 20])
        assert _score(c) == 3 - 2

    def test_footer_penalizes(self) -> None:
        c = self._base(inFooter=True)
        assert _score(c) == 3 - 1

    def test_aria_hidden_penalizes(self) -> None:
        c = self._base(ariaHidden=True)
        assert _score(c) == 3 - 1

    def test_skip_class_penalizes(self) -> None:
        c = self._base(skipClass=True)
        assert _score(c) == 3 - 1

    def test_name_bonus_only_within_range(self) -> None:
        # length 1 → no bonus
        c = self._base(name="a")
        assert _score(c) == 2
        # length 31 → no bonus
        c = self._base(name="a" * 31)
        assert _score(c) == 2

    def test_offscreen_gets_no_visibility_bonus(self) -> None:
        c = self._base(visible=True, inViewport=False)
        assert _score(c) == 1  # just the name bonus


class TestPickSelector:
    def test_role_and_name_wins(self) -> None:
        c = {"role": "button", "name": "Submit", "testId": "", "id": "", "tagName": "button"}
        assert _pick_selector(c) == 'role=button[name="Submit"]'

    def test_test_id_beats_id(self) -> None:
        c = {"role": "", "name": "", "testId": "primary-btn", "id": "b1", "tagName": "button"}
        assert _pick_selector(c) == '[data-testid="primary-btn"]'

    def test_id_beats_text(self) -> None:
        c = {"role": "", "name": "Click", "testId": "", "id": "b1", "tagName": "button"}
        assert _pick_selector(c) == "#b1"

    def test_text_fallback(self) -> None:
        c = {"role": "", "name": "Click me", "testId": "", "id": "", "tagName": "button"}
        assert _pick_selector(c) == 'text="Click me"'

    def test_tag_fallback(self) -> None:
        c = {"role": "", "name": "", "testId": "", "id": "", "tagName": "button"}
        assert _pick_selector(c) == "button"

    def test_quoting_escapes_double_quotes(self) -> None:
        c = {
            "role": "button",
            "name": 'She said "hi"',
            "testId": "",
            "id": "",
            "tagName": "button",
        }
        assert _pick_selector(c) == r'role=button[name="She said \"hi\""]'


class TestDedup:
    def _el(self, role: str, text: str, score: int) -> Element:
        return Element(
            selector=f'role={role}[name="{text}"]',
            role=role,
            text=text,
            bbox=(0, 0, 100, 30),
            score=score,
            source="dom-heuristic",
        )

    def test_same_role_and_text_keeps_higher_score(self) -> None:
        a = self._el("button", "Click", 3)
        b = self._el("button", "Click", 5)
        result = _dedup([a, b])
        assert len(result) == 1
        assert result[0].score == 5

    def test_different_text_preserved(self) -> None:
        a = self._el("button", "One", 3)
        b = self._el("button", "Two", 3)
        assert len(_dedup([a, b])) == 2

    def test_preserves_first_encounter_order(self) -> None:
        elements = [
            self._el("button", "B", 1),
            self._el("button", "A", 1),
            self._el("button", "B", 2),  # would replace but keep position
        ]
        result = _dedup(elements)
        assert [e.text for e in result] == ["B", "A"]
        assert result[0].score == 2


class TestDiscoverValidation:
    async def test_bad_limit(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            await discover("about:blank", limit=0)


FIXTURE_HTML = """<!DOCTYPE html>
<html><head><title>fixture</title></head>
<body>
  <nav>
    <a href="/skip" class="nav-skip">Skip to content</a>
  </nav>
  <main>
    <button id="btn3D">3D</button>
    <button aria-label="Compare countries">Compare</button>
    <button data-testid="reset-btn">Reset</button>
    <a href="/rankings" role="link">Open Rankings</a>
  </main>
  <footer>
    <a href="/privacy">Privacy</a>
    <a href="/terms">Terms</a>
  </footer>
</body></html>
"""


@pytest_asyncio.fixture
async def loaded_session() -> AsyncIterator[Session]:
    async with Session(viewport=(800, 600)) as sess:
        await sess.page.set_content(FIXTURE_HTML)
        sess.page.set_default_timeout(3000)
        yield sess


@pytest.mark.integration
class TestDiscoverIntegration:
    async def test_returns_ranked_elements(self, loaded_session: Session) -> None:
        elements = await discover(loaded_session, limit=20)

        assert len(elements) >= 4  # 3 buttons + main-area link at minimum
        # Every element has an actionable selector
        assert all(e.selector for e in elements)
        assert all(e.source == "dom-heuristic" for e in elements)
        # Sorted by score desc
        scores = [e.score for e in elements]
        assert scores == sorted(scores, reverse=True)

    async def test_main_buttons_outrank_footer_links(self, loaded_session: Session) -> None:
        # Roadmap acceptance: nav + 3 primary buttons + footer → buttons first.
        elements = await discover(loaded_session, limit=20)
        top4_texts = [e.text for e in elements[:4]]
        assert "3D" in top4_texts
        assert any("Compare" in t for t in top4_texts)
        # Footer links should not be in the top slots
        for text in ("Privacy", "Terms"):
            top4_only_texts = top4_texts[:3]
            assert text not in top4_only_texts

    async def test_limit_caps_result(self, loaded_session: Session) -> None:
        elements = await discover(loaded_session, limit=2)
        assert len(elements) <= 2

    async def test_element_serialization(self, loaded_session: Session) -> None:
        elements = await discover(loaded_session, limit=1)
        assert elements
        d = elements[0].to_dict()
        assert set(d.keys()) == {"selector", "role", "text", "bbox", "score", "source"}
        assert isinstance(d["bbox"], list)
        assert len(d["bbox"]) == 4

    async def test_read_only_does_not_fire_events(self, loaded_session: Session) -> None:
        # Wire a listener that would flip the DOM if any click fires
        await loaded_session.page.evaluate(
            """
            () => {
              window.__clicked = false;
              document.querySelectorAll('button, a').forEach(el =>
                el.addEventListener('click', () => { window.__clicked = true; })
              );
            }
            """
        )
        await discover(loaded_session, limit=20)
        clicked = await loaded_session.page.evaluate("() => window.__clicked")
        assert clicked is False

    async def test_selector_targets_a_real_element(self, loaded_session: Session) -> None:
        elements = await discover(loaded_session, limit=5)
        assert elements
        for e in elements[:3]:
            # Every top selector should locate at least one element
            count = await loaded_session.page.locator(e.selector).count()
            assert count >= 1, f"Selector did not match: {e.selector!r}"
