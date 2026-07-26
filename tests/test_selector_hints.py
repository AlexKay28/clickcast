"""Tests for :mod:`clickcast.discovery.hints` (issue #114).

Covers the pure scoring / formatting layer with mocked discovery, plus an
integration test on a synthetic page where the target is ``role=tab`` but
the scenario asked for ``role=button`` — the exact real-world miss that
motivated the diagnostics work. Also verifies the empty-pool degradation
path and that the actions.py failure hook actually augments the error
string on missed clicks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from clickcast.core.actions import ClickStep, execute
from clickcast.core.session import Session
from clickcast.discovery import Element
from clickcast.discovery.hints import (
    ScoredCandidate,
    format_candidates,
    parse_selector,
    suggest_candidates,
)

# ---------------------------------------------------------------------------
# parse_selector — unit
# ---------------------------------------------------------------------------


class TestParseSelector:
    def test_role_and_name(self) -> None:
        assert parse_selector('role=button[name="Population"]') == ("button", "Population")

    def test_role_only(self) -> None:
        # No `[name=...]` fragment — name comes back None; role still parsed.
        assert parse_selector("role=tab") == ("tab", None)

    def test_neither(self) -> None:
        assert parse_selector("#some-id") == (None, None)

    def test_single_quoted_name(self) -> None:
        assert parse_selector("role=link[name='Docs']") == ("link", "Docs")

    def test_name_only_pattern(self) -> None:
        # A selector fragment with `[name=...]` but no `role=` — role is None,
        # name comes through. This shape isn't common but the parser doesn't
        # blow up on it.
        assert parse_selector('[name="Save"]') == (None, "Save")


# ---------------------------------------------------------------------------
# format_candidates — unit
# ---------------------------------------------------------------------------


def _mkel(role: str, text: str, selector: str) -> Element:
    return Element(
        selector=selector,
        role=role,
        text=text,
        bbox=(10, 20, 100, 30),
        score=1,
        source="dom-heuristic",
    )


class TestFormatCandidates:
    def test_empty_pool_gives_clean_message(self) -> None:
        out = format_candidates('role=button[name="X"]', [], total_discovered=0)
        assert "resolved to 0 elements" in out
        assert "No interactive elements discovered" in out
        # No crash and no dangling "Candidates" header.
        assert "Candidates" not in out

    def test_with_candidates_lists_and_shows_total(self) -> None:
        cands = [
            ScoredCandidate(_mkel("tab", "Population", 'role=tab[name="Population"]'), 0.87),
            ScoredCandidate(_mkel("button", "Populate", 'role=button[name="Populate"]'), 0.72),
        ]
        out = format_candidates('role=button[name="Population"]', cands, total_discovered=47)
        assert 'role=tab[name="Population"]' in out
        assert "score=0.87" in out
        assert "score=0.72" in out
        assert "Full page discovery: 47 interactive elements" in out
        assert "--dump-elements" in out

    def test_dump_hint_toggle_hides_rerun_suggestion(self) -> None:
        cands = [ScoredCandidate(_mkel("tab", "X", 'role=tab[name="X"]'), 0.5)]
        out = format_candidates("role=x", cands, total_discovered=3, dump_hint=False)
        assert "--dump-elements" not in out


# ---------------------------------------------------------------------------
# suggest_candidates — unit (mocked discover)
# ---------------------------------------------------------------------------


class TestSuggestCandidatesRanking:
    """Verifies the scoring model without spinning up a browser: mock
    :func:`clickcast.discovery.hints.discover` to return a controlled pool.
    """

    async def test_top1_is_role_mismatch_tab_when_names_align(self) -> None:
        pool = [
            _mkel("tab", "Population", 'role=tab[name="Population"]'),
            _mkel("button", "Populate", 'role=button[name="Populate"]'),
            _mkel("link", "Population data", 'role=link[name="Population data"]'),
            _mkel("button", "Continue", 'role=button[name="Continue"]'),
            _mkel("button", "Cancel", 'role=button[name="Cancel"]'),
        ]
        with patch(
            "clickcast.discovery.hints.discover",
            new=AsyncMock(return_value=pool),
        ):
            hits = await suggest_candidates(
                session=object(),  # type: ignore[arg-type]
                failed_selector='role=button[name="Population"]',
                top_n=5,
            )

        assert len(hits) == 5
        # Top-1 has the exact target name — the whole point of the feature.
        assert hits[0].element.text == "Population"
        assert hits[0].element.role == "tab"
        # And it beats the true role-matching but name-different "Populate".
        assert hits[0].score > hits[1].score

    async def test_top_n_sorted_desc(self) -> None:
        pool = [
            _mkel("button", "AAAA", 'role=button[name="AAAA"]'),
            _mkel("button", "AAAB", 'role=button[name="AAAB"]'),
            _mkel("button", "ZZZZ", 'role=button[name="ZZZZ"]'),
            _mkel("link", "AAAA", 'role=link[name="AAAA"]'),
        ]
        with patch(
            "clickcast.discovery.hints.discover",
            new=AsyncMock(return_value=pool),
        ):
            hits = await suggest_candidates(
                session=object(),  # type: ignore[arg-type]
                failed_selector='role=button[name="AAAA"]',
                top_n=4,
            )
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True), scores

    async def test_empty_pool_returns_empty_list(self) -> None:
        with patch(
            "clickcast.discovery.hints.discover",
            new=AsyncMock(return_value=[]),
        ):
            hits = await suggest_candidates(
                session=object(),  # type: ignore[arg-type]
                failed_selector='role=button[name="Anything"]',
                top_n=5,
            )
        assert hits == []

    async def test_top_n_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="top_n"):
            await suggest_candidates(
                session=object(),  # type: ignore[arg-type]
                failed_selector="x",
                top_n=0,
            )


# ---------------------------------------------------------------------------
# Integration — real fixture page with role=tab labeled "Population"
# ---------------------------------------------------------------------------


_FIXTURE_HTML = """<!DOCTYPE html>
<html><head><title>hints fixture</title></head>
<body>
  <nav>
    <div role="tab" tabindex="0" aria-label="Population">Population</div>
    <div role="tab" tabindex="0" aria-label="Geography">Geography</div>
  </nav>
  <main>
    <button>Populate</button>
    <button>Continue</button>
    <button>Cancel</button>
    <a href="/x" role="link">Population data</a>
  </main>
</body></html>
"""


@pytest_asyncio.fixture
async def hints_session() -> AsyncIterator[Session]:
    async with Session(viewport=(800, 600)) as sess:
        await sess.page.set_content(_FIXTURE_HTML)
        sess.page.set_default_timeout(2000)
        yield sess


@pytest.mark.integration
class TestSuggestCandidatesIntegration:
    async def test_top1_is_the_intended_role_tab(self, hints_session: Session) -> None:
        hits = await suggest_candidates(
            hints_session,
            'role=button[name="Population"]',
            top_n=5,
        )
        assert hits, "expected candidates from a non-empty discovery pool"
        top = hits[0].element
        assert top.text == "Population"
        assert top.role == "tab"

    async def test_click_failure_augments_error_with_hints(self, hints_session: Session) -> None:
        step = ClickStep(
            selector='role=button[name="Population"]',
            timeout_ms=500,  # keep the test fast — no need for the 30s default
        )
        result = await execute(step, hints_session)
        assert not result.ok
        assert result.error is not None
        # Original TimeoutError still there.
        assert "Timeout" in result.error or "0 elements" in result.error
        # And the augmented block shows the actual tab as a hint.
        assert "Candidates that might be what you meant" in result.error
        assert 'role=tab[name="Population"]' in result.error

    async def test_empty_page_click_failure_gives_clean_message(self) -> None:
        async with Session(viewport=(400, 300)) as sess:
            await sess.page.set_content("<!DOCTYPE html><html><body></body></html>")
            sess.page.set_default_timeout(1000)
            step = ClickStep(
                selector='role=button[name="Nope"]',
                timeout_ms=300,
            )
            result = await execute(step, sess)
        assert not result.ok
        assert result.error is not None
        assert "No interactive elements discovered" in result.error
