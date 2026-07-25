"""Verify that `Config.load()` layers actually reach CLI subcommand defaults.

Before this fix (#41), `clickcast` shipped a full layered-precedence Config
that no CLI command consumed — `CLICKCAST_ENGINE=firefox clickcast auto ...`
silently ran chromium. These tests lock in the wire so we don't regress.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clickcast.cli import app

runner = CliRunner()


class TestCliDefaultsFromEnv:
    """Env vars must reach subcommand defaults via `default_map`."""

    def test_env_engine_reaches_auto(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Point Config at empty tmp files so only the env var is in play.
        monkeypatch.setenv("CLICKCAST_ENGINE", "firefox")
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )

        captured: dict[str, object] = {}
        # Stop before actually launching a browser — assert the resolved engine.

        async def _fake_do_auto(**kwargs: object) -> None:
            captured.update(kwargs["session_kwargs"])  # type: ignore[arg-type]

        with (
            patch("clickcast.cli._do_auto", side_effect=_fake_do_auto),
            patch("clickcast.cli.asyncio.run", lambda coro: coro.send(None) if False else None),
        ):
            # Actually just call auto and let it fail at the Session step.
            # Simpler: patch _do_auto to record and return.
            pass

        # Simpler approach: patch `_do_auto` at the source and let asyncio.run
        # invoke our sync capture-only stub.
        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_auto", side_effect=_capture):
            r = runner.invoke(
                app,
                ["auto", "data:text/html,x", "--out", str(tmp_path / "x.gif")],
            )
        assert r.exit_code == 0, r.output
        assert captured["session_kwargs"]["engine"] == "firefox"  # type: ignore[index]

    def test_explicit_flag_wins_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLICKCAST_ENGINE", "firefox")
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_auto", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "auto",
                    "data:text/html,x",
                    "--engine",
                    "chromium",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code == 0, r.output
        assert captured["session_kwargs"]["engine"] == "chromium"  # type: ignore[index]


class TestCliDefaultsFromToml:
    def test_user_toml_reaches_auto(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        user = tmp_path / "user.toml"
        user.write_text('engine = "webkit"\n')

        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=user,
                **kw,
            ),
        )

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_auto", side_effect=_capture):
            r = runner.invoke(
                app,
                ["auto", "data:text/html,x", "--out", str(tmp_path / "x.gif")],
            )
        assert r.exit_code == 0, r.output
        assert captured["session_kwargs"]["engine"] == "webkit"  # type: ignore[index]


class TestScenarioMetaWinsOverConfig:
    """For `clickcast run`, scenario meta beats env / TOML but loses to explicit CLI flags."""

    def _scenario(self, tmp_path: Path, meta_headful: bool = False) -> Path:
        p = tmp_path / "s.yml"
        p.write_text(
            f"""
            meta:
              headful: {"true" if meta_headful else "false"}
            steps: []
            """
        )
        return p

    def test_env_headful_does_not_override_meta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLICKCAST_HEADFUL", "true")
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )
        scenario = self._scenario(tmp_path, meta_headful=False)

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)
            # Return so we don't try to open a browser.

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(app, ["run", str(scenario), "--out", str(tmp_path / "x.gif")])
        assert r.exit_code == 0, r.output
        # Meta's headful=false wins over env's true.
        assert captured["scenario"].meta.headful is False  # type: ignore[union-attr]

    def test_explicit_headful_overrides_meta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )
        scenario = self._scenario(tmp_path, meta_headful=False)

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "run",
                    str(scenario),
                    "--headful",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code == 0, r.output
        assert captured["scenario"].meta.headful is True  # type: ignore[union-attr]


class TestConfigDoesNotBreakOnMissing:
    """A broken config file must not brick the CLI."""

    def test_help_still_works_when_config_load_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(**kw: object) -> None:
            raise RuntimeError("simulated broken config")

        monkeypatch.setattr("clickcast.cli.load_config", _raise)
        r = runner.invoke(app, ["--help"])
        assert r.exit_code == 0
        assert "clickcast" in r.stdout.lower()


class TestConfigDefaultMapIsAutoDerived:
    """`_config_default_map()` must be derived from Typer command signatures.

    Regression guard for #84: before this, a hand-written
    `_CONFIG_KEYS_PER_COMMAND` dict shadowed the Typer app and silently
    dropped any newly-added Config field until someone remembered to update
    the map. The map now falls out of introspection over
    ``app.registered_commands``.
    """

    def test_keys_are_registered_command_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )
        from clickcast.cli import _config_default_map, app

        expected = {(cmd.name or cmd.callback.__name__) for cmd in app.registered_commands}
        assert set(_config_default_map().keys()) == expected

    def test_values_are_subset_of_config_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )
        from clickcast.cli import _config_default_map
        from clickcast.config import Config

        field_names = set(Config.model_fields)
        for cmd, defaults in _config_default_map().items():
            assert set(defaults).issubset(field_names), (
                f"{cmd} default_map exposed non-Config keys: {set(defaults) - field_names}"
            )

    def test_ctx_is_not_leaked_as_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ctx: typer.Context` appears in some callback signatures (auto, run)
        but must NOT show up in the resolved default_map — Click would explode
        trying to inject a Context as a keyword default.
        """
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )
        from clickcast.cli import _config_default_map

        for cmd, defaults in _config_default_map().items():
            assert "ctx" not in defaults, f"{cmd} leaked ctx: {defaults}"

    def test_new_config_field_pace_reaches_auto(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end check: the sibling of #77 (`pace`) reaches `auto` via env.

        This is the concrete regression #84 was worried about — a Config
        field is added, but the hand-written map isn't updated, and the env
        var silently doesn't take effect. Introspection kills that class of
        bug.
        """
        monkeypatch.setenv("CLICKCAST_PACE", "slow")
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_auto", side_effect=_capture):
            r = runner.invoke(
                app,
                ["auto", "data:text/html,x", "--out", str(tmp_path / "x.gif")],
            )
        assert r.exit_code == 0, r.output
        # `slow` preset → (fps=10, dwell=0.7); the pace default reaching the
        # command is what proves the map wire is live for the pace field.
        assert captured["fps"] == 10
        assert captured["dwell"] == pytest.approx(0.7)
