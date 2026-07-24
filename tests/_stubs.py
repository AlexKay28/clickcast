"""Shared Playwright / Recorder stubs for `tests/test_cli_auto_*.py`.

Five test files used to redefine near-identical `_FakePage`, `_FakeSession`,
`_make_element`, `_make_result`, and `_stub_environment` — ~500 lines of
duplicated infrastructure. Centralized here per #79. The canonical `FakePage`
includes every field any test used to track (url stack, go_back history,
go_back kwargs), so a new test never has to reintroduce them.

Pair with :mod:`tests.conftest` for the `stub_environment` pytest fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from clickcast.discovery import Element

__all__ = [
    "FakePage",
    "FakeRecorder",
    "FakeSession",
    "make_element",
    "make_result",
]


def make_element(text: str) -> Element:
    """Build a plausible :class:`Element` for tests. All discovered fields
    are set so the test doesn't have to think about them."""
    return Element(
        selector=f'text="{text}"',
        role="link",
        text=text,
        bbox=(100, 80, 100, 30),
        score=3,
        source="dom-heuristic",
    )


def make_result(*, ok: bool = True, cursor: tuple[int, int] | None = (100, 80)) -> MagicMock:
    """Build a plausible :class:`ActionResult`-shaped MagicMock."""
    r = MagicMock()
    r.ok = ok
    r.status = "ok" if ok else "failed"
    r.error = None if ok else "boom"
    r.cursor_xy = cursor
    return r


class FakePage:
    """Just enough of :class:`playwright.async_api.Page` to satisfy the auto
    engine's ``page.url`` reads and ``page.go_back()`` calls.

    Assigning to ``url`` pushes onto a history stack so ``go_back()`` can
    restore the previous URL — the click-and-observe drift-detection path
    (:pr:`56`) uses this. Tests inspect:

    - ``page.url`` — current URL (top of stack).
    - ``page.go_back_history`` — URLs go_back was called FROM.
    - ``page.go_back_kwargs`` — kwargs each go_back call received (e.g. to
      assert ``wait_until="domcontentloaded"`` is passed per :pr:`58`).
    """

    def __init__(self) -> None:
        self._url_stack: list[str] = [""]
        self.go_back_history: list[str] = []
        self.go_back_kwargs: list[dict[str, Any]] = []

    @property
    def url(self) -> str:
        return self._url_stack[-1]

    @url.setter
    def url(self, new: str) -> None:
        self._url_stack.append(new)

    async def go_back(self, **kwargs: Any) -> None:
        self.go_back_history.append(self._url_stack[-1])
        self.go_back_kwargs.append(kwargs)
        if len(self._url_stack) > 1:
            self._url_stack.pop()


class FakeSession:
    """Minimal :class:`clickcast.core.session.Session` substitute."""

    def __init__(self) -> None:
        self.page = FakePage()

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def wait(self, _s: float | None) -> None:
        return None


class FakeRecorder:
    """Bare-bones Recorder that satisfies the auto engine's calls but doesn't
    actually take screenshots. Tests only care about the sequence of actions
    the engine drives — not the frames themselves."""

    def __init__(self, tmp_path: Path, **_kwargs: Any) -> None:
        self.frames_dir = tmp_path / "frames"
        self.frames_dir.mkdir(exist_ok=True)

    def __enter__(self) -> FakeRecorder:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    async def pre_action(self, *_a: Any, **_kw: Any) -> Path:
        return self.frames_dir / "p.png"

    async def post_action(self, *_a: Any, **_kw: Any) -> list[Path]:
        return [self.frames_dir / "q.png"]

    def flush(self) -> list[Path]:
        return []
