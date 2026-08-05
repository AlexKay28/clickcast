"""CLI wiring for #166: --insecure / --header / --header-host across
``shot`` / ``auto`` / ``run`` / ``elements``, plus env-var precedence
via the layered Config."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clickcast.cli import _parse_header_flags, app

runner = CliRunner()


class TestParseHeaderFlags:
    """The `--header "Name: value"` splitter used by every command."""

    def test_none_returns_empty(self) -> None:
        assert _parse_header_flags(None) == {}
        assert _parse_header_flags([]) == {}

    def test_single_header(self) -> None:
        assert _parse_header_flags(["Authorization: Bearer x"]) == {"Authorization": "Bearer x"}

    def test_value_with_colon_preserved(self) -> None:
        """Splitting on the FIRST colon lets header values (URLs, times)
        contain colons freely."""
        assert _parse_header_flags(["X-Time: 12:00:00"]) == {"X-Time": "12:00:00"}

    def test_whitespace_trimmed_around_name_and_value(self) -> None:
        assert _parse_header_flags(["  X-Foo  :  bar  "]) == {"X-Foo": "bar"}

    def test_duplicate_name_last_wins(self) -> None:
        """Matches Playwright's ``extra_http_headers`` semantics — a dict
        can only hold one value per name."""
        assert _parse_header_flags(["X-A: 1", "X-A: 2"]) == {"X-A": "2"}

    def test_no_colon_raises(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            _parse_header_flags(["bogus"])

    def test_empty_name_raises(self) -> None:
        import typer

        with pytest.raises(typer.BadParameter):
            _parse_header_flags([": value"])


class TestShotFlags:
    """`shot` gets all three flags directly (no scenario merge)."""

    def test_insecure_reaches_session_kwargs(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_shot", side_effect=_capture):
            r = runner.invoke(
                app,
                ["shot", "data:text/html,x", "--out", str(tmp_path / "s.png"), "--insecure"],
            )
        assert r.exit_code == 0, r.output
        assert captured["session_kwargs"]["ignore_https_errors"] is True  # type: ignore[index]

    def test_header_and_host_reach_session_kwargs(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_shot", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "shot",
                    "data:text/html,x",
                    "--out",
                    str(tmp_path / "s.png"),
                    "--header",
                    "Authorization: Bearer x",
                    "--header-host",
                    "internal.example.com",
                ],
            )
        assert r.exit_code == 0, r.output
        assert captured["session_kwargs"]["extra_http_headers"] == {  # type: ignore[index]
            "Authorization": "Bearer x"
        }
        assert (
            captured["session_kwargs"]["header_host"] == "internal.example.com"  # type: ignore[index]
        )


class TestAutoFlags:
    def test_all_three_flags_reach_session_kwargs(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_auto", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "auto",
                    "data:text/html,x",
                    "--out",
                    str(tmp_path / "r.gif"),
                    "--insecure",
                    "--header",
                    "Authorization: Bearer x",
                    "--header",
                    "X-Trace: 1",
                    "--header-host",
                    "internal.example.com",
                ],
            )
        assert r.exit_code == 0, r.output
        sk = captured["session_kwargs"]  # type: ignore[index]
        assert sk["ignore_https_errors"] is True  # type: ignore[index]
        assert sk["extra_http_headers"] == {  # type: ignore[index]
            "Authorization": "Bearer x",
            "X-Trace": "1",
        }
        assert sk["header_host"] == "internal.example.com"  # type: ignore[index]


class TestElementsFlags:
    def test_insecure_and_header(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> list[object]:
            captured.update(kwargs)
            return []

        with patch("clickcast.cli._do_elements", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "elements",
                    "data:text/html,x",
                    "--insecure",
                    "--header",
                    "Authorization: Bearer x",
                    "--header-host",
                    "internal.example.com",
                ],
            )
        assert r.exit_code == 0, r.output
        sk = captured["session_kwargs"]  # type: ignore[index]
        assert sk["ignore_https_errors"] is True  # type: ignore[index]
        assert sk["extra_http_headers"] == {"Authorization": "Bearer x"}  # type: ignore[index]
        assert sk["header_host"] == "internal.example.com"  # type: ignore[index]


class TestRunFlagsMergeIntoMeta:
    """`run` mutates scenario.meta.browser only when the flag was typed —
    same explicit-vs-default pattern as --headful."""

    def _scenario(self, tmp_path: Path) -> Path:
        p = tmp_path / "s.yml"
        p.write_text("meta: {}\nsteps: []\n")
        return p

    def test_explicit_insecure_wins_over_meta(self, tmp_path: Path) -> None:
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "run",
                    str(self._scenario(tmp_path)),
                    "--out",
                    str(tmp_path / "r.gif"),
                    "--insecure",
                    "--header",
                    "Authorization: Bearer x",
                    "--header-host",
                    "internal.example.com",
                ],
            )
        assert r.exit_code == 0, r.output
        meta = captured["scenario"].meta  # type: ignore[union-attr]
        assert meta.browser.insecure is True
        assert meta.browser.extra_headers == {"Authorization": "Bearer x"}
        assert meta.browser.header_host == "internal.example.com"

    def test_meta_wins_when_flag_not_typed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env-var CLICKCAST_INSECURE=1 does NOT override scenario meta
        for `run` — matches how CLICKCAST_HEADFUL behaves. Users should
        put scenario-level defaults into the YAML itself."""
        monkeypatch.setenv("CLICKCAST_INSECURE", "true")
        monkeypatch.setattr(
            "clickcast.cli.load_config",
            lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
                project_toml=tmp_path / "p.toml",
                user_toml=tmp_path / "u.toml",
                **kw,
            ),
        )
        scenario = tmp_path / "s.yml"
        scenario.write_text("meta:\n  browser:\n    insecure: false\nsteps: []\n")

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(
                app,
                ["run", str(scenario), "--out", str(tmp_path / "r.gif")],
            )
        assert r.exit_code == 0, r.output
        assert captured["scenario"].meta.browser.insecure is False  # type: ignore[union-attr]


class TestScenarioMetaFlatShimAcceptsInsecureFields:
    """The `_migrate_flat_to_nested` shim in Meta must know about the new
    BrowserOpts fields so users can write them at meta root, not only under
    `meta.browser:`."""

    def test_flat_insecure_and_header_host(self) -> None:
        from clickcast.scenario import loads

        scenario = loads(
            """
            meta:
              insecure: true
              header_host: internal.example.com
            steps: []
            """
        )
        assert scenario.meta.browser.insecure is True
        assert scenario.meta.browser.header_host == "internal.example.com"


class TestEnvVarReachesShot:
    """CLICKCAST_INSECURE=1 must reach `shot` via default_map."""

    def test_insecure_env_reaches_shot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLICKCAST_INSECURE", "true")
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

        with patch("clickcast.cli._do_shot", side_effect=_capture):
            r = runner.invoke(
                app,
                ["shot", "data:text/html,x", "--out", str(tmp_path / "s.png")],
            )
        assert r.exit_code == 0, r.output
        assert captured["session_kwargs"]["ignore_https_errors"] is True  # type: ignore[index]
