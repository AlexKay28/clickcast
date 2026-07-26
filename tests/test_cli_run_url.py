"""`clickcast run --url` — first-class URL override for scenarios (#115).

The `--url` flag rewrites the first `goto` step's URL after the scenario is
loaded (and after `--var` substitution). It's a QoL shortcut for pointing an
existing scenario at a different environment (staging / PR preview /
localhost) without editing the YAML or authoring `{{ URL }}` templating.

Precedence (highest wins): ``--url`` > ``--var URL=...`` > scenario URL.
Only the FIRST goto is touched — later goto steps stay untouched because
they're usually intra-app navigation from the entry point.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from clickcast.cli import app

runner = CliRunner()


def _write_scenario(path: Path, body: str) -> Path:
    """Dedent-free helper — write a scenario file at `path`."""
    path.write_text(body)
    return path


class TestUrlOverride:
    def test_url_flag_rewrites_first_goto(self, tmp_path: Path) -> None:
        """`--url` swaps the first `goto` step's URL and leaves everything else."""
        scenario = _write_scenario(
            tmp_path / "s.yml",
            """\
meta:
  name: t
steps:
  - goto: https://original.example.com
    label: home
  - click: "#btn"
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
        scn = captured["scenario"]
        assert scn.steps[0].action == "goto"  # type: ignore[union-attr]
        assert scn.steps[0].url == "https://override.example.com"  # type: ignore[union-attr]
        # Sibling step is untouched.
        assert scn.steps[1].action == "click"  # type: ignore[union-attr]
        assert scn.steps[1].selector == "#btn"  # type: ignore[union-attr]

    def test_url_wins_over_var(self, tmp_path: Path) -> None:
        """When both `--url` and `--var URL=...` are given, `--url` wins."""
        scenario = _write_scenario(
            tmp_path / "s.yml",
            """\
meta:
  name: t
steps:
  - goto: "{{ URL }}"
    label: home
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
                    "URL=https://var-lost.example.com",
                    "--url",
                    "https://flag-wins.example.com",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code == 0, r.output
        scn = captured["scenario"]
        assert scn.steps[0].url == "https://flag-wins.example.com"  # type: ignore[union-attr]

    def test_scenario_url_used_when_flag_absent(self, tmp_path: Path) -> None:
        """No `--url` → scenario's baked-in URL is preserved untouched."""
        scenario = _write_scenario(
            tmp_path / "s.yml",
            """\
meta:
  name: t
steps:
  - goto: https://baked-in.example.com
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
        scn = captured["scenario"]
        assert scn.steps[0].url == "https://baked-in.example.com"  # type: ignore[union-attr]

    def test_no_goto_step_raises_clear_error(self, tmp_path: Path) -> None:
        """Scenario with no `goto` step + `--url` → clear failure via `_die`."""
        scenario = _write_scenario(
            tmp_path / "s.yml",
            """\
meta:
  name: no-goto
steps:
  - click: "#btn"
  - scroll:
      by: 400
""",
        )

        # `_do_run` should never run — we bail out before then.
        async def _explode(**kwargs: object) -> None:
            raise AssertionError("_do_run must not be called when --url has nowhere to land")

        with patch("clickcast.cli._do_run", side_effect=_explode):
            r = runner.invoke(
                app,
                [
                    "run",
                    str(scenario),
                    "--url",
                    "https://x.example.com",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code == 1
        # Users need to know *why* it failed — spot-check the error is
        # actionable, not a stack trace.
        assert "goto" in (r.output + (r.stderr if r.stderr else "")).lower()

    def test_only_first_goto_rewritten_when_multiple(self, tmp_path: Path) -> None:
        """Scenarios with multiple gotos: only the first is retargeted."""
        scenario = _write_scenario(
            tmp_path / "s.yml",
            """\
meta:
  name: multi-goto
steps:
  - goto: https://entry.example.com
    label: entry
  - click: "#login"
  - goto: https://intra.example.com/dashboard
    label: after-login-nav
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
                    "https://staging.example.com",
                    "--out",
                    str(tmp_path / "x.gif"),
                ],
            )
        assert r.exit_code == 0, r.output
        scn = captured["scenario"]
        # First goto rewritten…
        assert scn.steps[0].url == "https://staging.example.com"  # type: ignore[union-attr]
        # …second goto left alone (intra-app navigation from the entry point).
        assert scn.steps[2].url == "https://intra.example.com/dashboard"  # type: ignore[union-attr]

    def test_help_mentions_url_flag(self) -> None:
        """`clickcast run --help` surfaces the new `--url` flag."""
        r = runner.invoke(app, ["run", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
        assert r.exit_code == 0
        # Typer's rich renderer wraps long flag lists across lines and injects
        # ANSI codes on CI even when NO_COLOR is set. Strip ANSI + collapse
        # whitespace so the check is layout-agnostic.
        plain = re.sub(r"\x1b\[[0-9;]*m", "", r.stdout)
        collapsed = re.sub(r"\s+", " ", plain)
        assert "--url" in collapsed
