"""Missing-browser-engine pre-flight UX: self-heal on a tty, fail clearly
and immediately when non-interactive (CI, piped input, etc.).

Before this, any browser-launching command run before `clickcast install`
surfaced Playwright's raw "Executable doesn't exist" traceback — the #1
"why doesn't this work" failure mode for a fresh `pip install clickcast`.
`Session.__aenter__` (tests/test_session.py) now raises a typed
`EngineNotInstalledError` before Playwright even starts; this file covers
what the CLI does with that error: `_handle_missing_engine` (the prompt/
install/die decision) and `_run` (the asyncio.run + one-retry wrapper every
browser-launching command goes through).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from clickcast.cli import _handle_missing_engine, _run, app
from clickcast.core.engines import EngineNotInstalledError

runner = CliRunner()

pytestmark = pytest.mark.unit


class TestHandleMissingEngine:
    def test_non_interactive_dies_immediately_without_prompting(self) -> None:
        with (
            patch("clickcast.cli.sys.stdin.isatty", return_value=False),
            patch("clickcast.cli.typer.confirm") as confirm,
            pytest.raises(typer.Exit) as exc_info,
        ):
            _handle_missing_engine(EngineNotInstalledError("chromium"))
        assert exc_info.value.exit_code == 1
        confirm.assert_not_called()

    def test_interactive_confirm_yes_installs_and_returns(self) -> None:
        with (
            patch("clickcast.cli.sys.stdin.isatty", return_value=True),
            patch("clickcast.cli.typer.confirm", return_value=True),
            patch("clickcast.cli._install_engine", return_value=0) as install,
        ):
            _handle_missing_engine(EngineNotInstalledError("firefox"))  # must not raise
        install.assert_called_once_with(["firefox"], with_deps=True)

    def test_interactive_confirm_no_dies(self) -> None:
        with (
            patch("clickcast.cli.sys.stdin.isatty", return_value=True),
            patch("clickcast.cli.typer.confirm", return_value=False),
            patch("clickcast.cli._install_engine") as install,
            pytest.raises(typer.Exit) as exc_info,
        ):
            _handle_missing_engine(EngineNotInstalledError("chromium"))
        assert exc_info.value.exit_code == 1
        install.assert_not_called()

    def test_interactive_confirm_yes_but_install_fails_dies(self) -> None:
        with (
            patch("clickcast.cli.sys.stdin.isatty", return_value=True),
            patch("clickcast.cli.typer.confirm", return_value=True),
            patch("clickcast.cli._install_engine", return_value=1),
            pytest.raises(typer.Exit) as exc_info,
        ):
            _handle_missing_engine(EngineNotInstalledError("chromium"))
        assert exc_info.value.exit_code == 1


class TestRun:
    # `_run` calls `asyncio.run()` itself, so these are plain `def` tests —
    # an `async def` test would already be inside pytest-asyncio's event
    # loop, and `asyncio.run()` refuses to nest.

    def test_retries_once_after_self_heal(self) -> None:
        calls = {"n": 0}

        async def flaky() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise EngineNotInstalledError("chromium")
            return "ok"

        with (
            patch("clickcast.cli.sys.stdin.isatty", return_value=True),
            patch("clickcast.cli.typer.confirm", return_value=True),
            patch("clickcast.cli._install_engine", return_value=0),
        ):
            result = _run(lambda: flaky())
        assert result == "ok"
        assert calls["n"] == 2

    def test_unrelated_exceptions_pass_through_untouched(self) -> None:
        async def boom() -> None:
            raise ValueError("not an engine problem")

        with pytest.raises(ValueError, match="not an engine problem"):
            _run(lambda: boom())


class TestEndToEnd:
    """Through the real Typer command, not just the helpers in isolation —
    proves Session -> EngineNotInstalledError -> _run -> _handle_missing_engine
    -> _die is actually wired together, not just individually correct."""

    def test_shot_command_dies_with_clear_message_when_engine_missing(
        self, tmp_path: object
    ) -> None:
        with patch("clickcast.core.session.find_installed_engine", return_value=None):
            result = runner.invoke(
                app,
                ["shot", "https://example.com", "--out", str(tmp_path) + "/x.png"],
            )
        assert result.exit_code == 1
        assert "chromium isn't installed" in result.output
        assert "clickcast install --with-deps chromium" in result.output
        # The old failure mode: a raw Playwright traceback leaking through.
        assert "Executable doesn't exist" not in result.output
        assert "Traceback" not in result.output
