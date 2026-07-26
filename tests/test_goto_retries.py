"""Retry-on-TimeoutError policy for `GotoStep`."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from clickcast.core.actions import GotoStep, execute


class _FakeSession:
    """Minimal session stub — only `.goto()` is exercised by GotoStep."""

    def __init__(self, side_effects: list[Any]) -> None:
        self.goto = AsyncMock(side_effect=side_effects)


class TestGotoRetries:
    async def test_no_retry_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No `retries=` on the step -> first TimeoutError propagates as failure.
        monkeypatch.setattr("clickcast.core.actions.asyncio.sleep", AsyncMock())
        sess = _FakeSession([PlaywrightTimeoutError("boom")])
        result = await execute(GotoStep(url="https://x"), sess)  # type: ignore[arg-type]
        assert not result.ok
        assert result.status == "failed"
        assert "TimeoutError" in (result.error or "")
        assert sess.goto.await_count == 1

    async def test_second_attempt_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Bypass backoff sleeps so the test stays fast.
        sleeper = AsyncMock()
        monkeypatch.setattr("clickcast.core.actions.asyncio.sleep", sleeper)
        sess = _FakeSession([PlaywrightTimeoutError("cold start"), None])
        result = await execute(GotoStep(url="https://x", retries=2), sess)  # type: ignore[arg-type]
        assert result.ok
        assert result.status == "ok"
        assert sess.goto.await_count == 2
        # First (and only) backoff should be 500ms.
        assert sleeper.await_count == 1
        assert sleeper.await_args_list[0].args == (0.5,)

    async def test_exhausts_retries_then_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeper = AsyncMock()
        monkeypatch.setattr("clickcast.core.actions.asyncio.sleep", sleeper)
        sess = _FakeSession([PlaywrightTimeoutError("boom")] * 3)
        result = await execute(GotoStep(url="https://x", retries=2), sess)  # type: ignore[arg-type]
        assert not result.ok
        assert result.status == "failed"
        # 1 initial + 2 retries = 3 attempts, but only 2 backoffs (between them).
        assert sess.goto.await_count == 3
        assert sleeper.await_count == 2
        # Exponential backoff: 500ms, 1s.
        assert sleeper.await_args_list[0].args == (0.5,)
        assert sleeper.await_args_list[1].args == (1.0,)

    async def test_non_timeout_error_does_not_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleeper = AsyncMock()
        monkeypatch.setattr("clickcast.core.actions.asyncio.sleep", sleeper)
        sess = _FakeSession([RuntimeError("dns broken")])
        result = await execute(GotoStep(url="https://x", retries=5), sess)  # type: ignore[arg-type]
        assert not result.ok
        assert "RuntimeError" in (result.error or "")
        assert sess.goto.await_count == 1  # no retries on non-Timeout errors
        assert sleeper.await_count == 0

    def test_step_field_defaults_and_validation(self) -> None:
        step = GotoStep(url="https://x")
        assert step.retries == 0
        with pytest.raises(Exception):  # noqa: B017
            GotoStep(url="https://x", retries=-1)
