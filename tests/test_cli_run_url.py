"""`clickcast run --url` — first-class URL override for scenarios (#115).

Precedence: `--url` > `--var URL=...` > scenario `meta.url` > scenario
`steps[0].url`. Only the FIRST `goto` step is rewritten — subsequent
`goto` steps might be intra-app navigation and stay untouched.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clickcast.cli import app
from clickcast.core.actions import GotoStep

runner = CliRunner()


def _scenario(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "tour.yml"
    p.write_text(body)
    return p


def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point Config at empty files so ambient env / user TOML can't leak in."""
    monkeypatch.setattr(
        "clickcast.cli.load_config",
        lambda **kw: __import__("clickcast.config", fromlist=["load"]).load(
            project_toml=tmp_path / "p.toml",
            user_toml=tmp_path / "u.toml",
            **kw,
        ),
    )


class TestUrlOverride:
    def test_url_flag_rewrites_first_goto(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_config(tmp_path, monkeypatch)
        scenario = _scenario(
            tmp_path,
            """
            meta:
              out: reel.gif
            steps:
              - goto: https://baked-in.example.com
                wait: load
                label: Open
            """,
        )

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "run",
                    str(scenario),
                    "--url",
                    "https://staging.example.com/app",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code == 0, r.output
        sc = captured["scenario"]
        first_goto = next(s for s in sc.steps if isinstance(s, GotoStep))  # type: ignore[union-attr]
        assert first_goto.url == "https://staging.example.com/app"

    def test_url_flag_wins_over_var_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_config(tmp_path, monkeypatch)
        scenario = _scenario(
            tmp_path,
            """
            meta:
              out: reel.gif
            steps:
              - goto: "{{ URL }}"
                wait: load
            """,
        )

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "run",
                    str(scenario),
                    "--var",
                    "URL=https://var-value.example.com",
                    "--url",
                    "https://flag-wins.example.com",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code == 0, r.output
        sc = captured["scenario"]
        first_goto = next(s for s in sc.steps if isinstance(s, GotoStep))  # type: ignore[union-attr]
        assert first_goto.url == "https://flag-wins.example.com"

    def test_url_flag_without_goto_step_errors_clearly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_config(tmp_path, monkeypatch)
        scenario = _scenario(
            tmp_path,
            """
            meta:
              out: reel.gif
            steps:
              - wait: 0.1
            """,
        )

        called = False

        async def _capture(**kwargs: object) -> None:
            nonlocal called
            called = True

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "run",
                    str(scenario),
                    "--url",
                    "https://nothing-to-override.example.com",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code != 0
        assert not called, "_do_run should not be invoked when --url has no target"
        # The error message goes to stderr; CliRunner merges by default.
        assert "no goto step" in r.output.lower()

    def test_url_flag_only_rewrites_first_of_many_gotos(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate_config(tmp_path, monkeypatch)
        scenario = _scenario(
            tmp_path,
            """
            meta:
              out: reel.gif
            steps:
              - goto: https://first.example.com
                wait: load
              - goto: https://second.example.com
                wait: load
              - goto: https://third.example.com
                wait: load
            """,
        )

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "run",
                    str(scenario),
                    "--url",
                    "https://override.example.com",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code == 0, r.output
        sc = captured["scenario"]
        gotos = [s for s in sc.steps if isinstance(s, GotoStep)]  # type: ignore[union-attr]
        assert [g.url for g in gotos] == [
            "https://override.example.com",
            "https://second.example.com",
            "https://third.example.com",
        ]

    def test_no_url_flag_leaves_scenario_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Absent --url, the baked-in goto URL passes through unchanged."""
        _isolate_config(tmp_path, monkeypatch)
        scenario = _scenario(
            tmp_path,
            """
            meta:
              out: reel.gif
            steps:
              - goto: https://baked-in.example.com
                wait: load
            """,
        )

        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_run", side_effect=_capture):
            r = runner.invoke(
                app,
                ["run", str(scenario), "--out", str(tmp_path / "x.gif")],
            )
        assert r.exit_code == 0, r.output
        sc = captured["scenario"]
        first_goto = next(s for s in sc.steps if isinstance(s, GotoStep))  # type: ignore[union-attr]
        assert first_goto.url == "https://baked-in.example.com"
