"""Tests for :mod:`clickcast.perception.settle` (#265).

Browser tests drive ``tests/fixtures/site/`` through a real Chromium session
(``integration``); the AST guard is a plain ``unit`` test. Assertions are on
``timed_out`` membership and relative ordering, never exact milliseconds, so
a loaded CI box doesn't turn them red.
"""

from __future__ import annotations

import ast
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from clickcast.core.session import Session
from clickcast.perception import (
    DEFAULT_BUDGET_MS,
    SIGNALS,
    SettleResult,
    settle,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PERCEPTION_DIR = _REPO_ROOT / "src" / "clickcast" / "perception"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[Session]:
    async with Session(viewport=(800, 600)) as sess:
        yield sess


def _url(fixture_site_url: str, page: str = "/") -> str:
    return fixture_site_url.rstrip("/") + page


# ---------------------------------------------------------------------------
# Pure result contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResultShape:
    def test_to_dict_matches_the_public_json_contract(self) -> None:
        result = SettleResult(
            ms=42,
            signals={"network": {"ok": True, "ms": 42, "budget_ms": 3000}},
            timed_out=(),
        )
        payload = result.to_dict()
        assert list(payload) == ["ok", "ms", "signals", "timed_out"]
        assert payload == {
            "ok": True,
            "ms": 42,
            "signals": {"network": {"ok": True, "ms": 42, "budget_ms": 3000}},
            "timed_out": [],
        }

    def test_ok_is_false_when_a_signal_timed_out(self) -> None:
        result = SettleResult(ms=2000, signals={}, timed_out=("raf",))
        assert result.ok is False
        assert result.to_dict()["ok"] is False
        assert result.to_dict()["timed_out"] == ["raf"]

    def test_signal_table_matches_the_documented_budgets(self) -> None:
        assert [(s.name, s.quiet_ms, s.budget_ms) for s in SIGNALS] == [
            ("network", 500, 3000),
            ("dom", 250, 2000),
            ("raf", 250, 2000),
        ]
        assert DEFAULT_BUDGET_MS == 3000


# ---------------------------------------------------------------------------
# No unbudgeted sleeps (mirrors tests/test_architecture.py's AST style)
# ---------------------------------------------------------------------------


def _perception_modules() -> list[Path]:
    return sorted(_PERCEPTION_DIR.rglob("*.py"))


def test_perception_has_no_unbudgeted_sleep() -> None:
    """Every ``asyncio.sleep`` under ``perception/`` must be deadline-capped."""
    violations: list[str] = []
    for path in _perception_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "sleep"
                and isinstance(func.value, ast.Name)
                and func.value.id == "asyncio"
            ):
                continue
            args = node.args
            budgeted = (
                len(args) == 1
                and isinstance(args[0], ast.Call)
                and isinstance(args[0].func, ast.Name)
                and args[0].func.id == "min"
                and any(
                    isinstance(name, ast.Name) and name.id == "deadline"
                    for name in ast.walk(args[0])
                )
            )
            if not budgeted:
                rel = path.relative_to(_REPO_ROOT).as_posix()
                violations.append(f"{rel}:{node.lineno}: asyncio.sleep is not capped by a deadline")
    assert not violations, "unbudgeted sleep in perception/:\n  " + "\n  ".join(violations)


# ---------------------------------------------------------------------------
# Live-session behaviour
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettleOnFixtureSite:
    async def test_repeated_settles_agree(self, session: Session, fixture_site_url: str) -> None:
        """N=5 consecutive settles: no flapping in ``ok``/``timed_out``."""
        await session.goto(_url(fixture_site_url), wait="load")
        outcomes = []
        for _ in range(5):
            result = await settle(session)
            assert isinstance(result, SettleResult)
            assert result.ms <= DEFAULT_BUDGET_MS + 500
            outcomes.append((result.ok, result.timed_out))
        assert outcomes[0] == (True, ())
        assert all(outcome == outcomes[0] for outcome in outcomes), outcomes

    async def test_quiet_page_settles_well_under_budget(
        self, session: Session, fixture_site_url: str
    ) -> None:
        await session.goto(_url(fixture_site_url), wait="load")
        budget_ms = 10_000
        result = await settle(session, budget_ms=budget_ms)
        assert result.ok is True
        assert result.timed_out == ()
        assert result.ms < budget_ms / 2

    async def test_mutating_page_settles_after_the_dom_stops(
        self, session: Session, fixture_site_url: str
    ) -> None:
        """The DOM signal waits out a ~1 s mutation stream, then succeeds."""
        await session.goto(_url(fixture_site_url, "/mutate-then-quiet.html"), wait="load")
        result = await settle(session, budget_ms=8000)
        assert result.ok is True
        assert "dom" not in result.timed_out
        assert result.signals["dom"]["mutations"] > 0
        # The fixture keeps mutating for ~1 s; an early "quiet" would be a bug.
        assert result.ms >= 700

    async def test_infinite_raf_page_reports_raf_timeout_within_budget(
        self, session: Session, fixture_site_url: str
    ) -> None:
        await session.goto(_url(fixture_site_url, "/infinite-raf.html"), wait="load")
        budget_ms = 3000
        started = time.monotonic()
        result = await settle(session, budget_ms=budget_ms)
        wall_s = time.monotonic() - started
        assert result.ok is False
        assert result.timed_out == ("raf",)
        # Network and DOM both quiet down on their own.
        assert result.signals["network"]["ok"] is True
        assert result.signals["dom"]["ok"] is True
        # raf's own 2000 ms budget < the 3000 ms overall budget.
        assert wall_s < budget_ms / 1000.0 + 1.0
        assert result.ms <= budget_ms + 500

    async def test_install_twice_is_idempotent(
        self, session: Session, fixture_site_url: str
    ) -> None:
        await session.goto(_url(fixture_site_url), wait="load")
        first = await settle(session)
        assert first.ok is True
        await session.evaluate(
            """() => {
              window.__ccMut.__ccTestMarker = 'mut';
              window.__ccRaf.__ccTestMarker = 'raf';
            }"""
        )
        second = await settle(session)
        assert second.ok is True
        markers = await session.evaluate(
            """() => ({
              mut: window.__ccMut && window.__ccMut.__ccTestMarker,
              raf: window.__ccRaf && window.__ccRaf.__ccTestMarker,
              mutObserver: !!(window.__ccMut && window.__ccMut.observer),
              rafWrapped: !!(window.__ccRaf && window.__ccRaf.wrapped),
            })"""
        )
        # Same state objects survived the second settle: no observer was
        # reinstalled or leaked.
        assert markers == {
            "mut": "mut",
            "raf": "raf",
            "mutObserver": True,
            "rafWrapped": True,
        }

    async def test_navigation_mid_settle_does_not_raise(
        self, session: Session, fixture_site_url: str
    ) -> None:
        await session.goto(_url(fixture_site_url), wait="load")
        await session.evaluate(
            """() => {
              setTimeout(() => { window.location.href = '/tabs.html'; }, 250);
            }"""
        )
        result = await settle(session, budget_ms=6000)
        assert isinstance(result, SettleResult)
        assert set(result.signals) == {"network", "dom", "raf"}
        assert result.ms <= 6500

    async def test_missing_get_animations_degrades_one_signal(
        self, session: Session, fixture_site_url: str
    ) -> None:
        await session.goto(_url(fixture_site_url), wait="load")
        await session.evaluate("() => { delete Document.prototype.getAnimations; }")
        result = await settle(session, budget_ms=6000)
        assert result.ok is True
        assert result.signals["raf"]["supported"] is False
        # The rAF half of the compositor signal still ran.
        assert "frames" in result.signals["raf"]
