"""``--for-humans`` composite flag on ``clickcast auto`` (#129 Track F).

The composite flag flips several sub-flags to human-friendly defaults:

- ``--pace onboarding`` (fps 8, dwell 1.2)
- ``--zoom-on-click 2.5``
- ``--highlight-target``
- ``--title-card``
- ``--summary-card``

Explicit user-supplied flags always win, mirroring the ``_is_explicit``
precedence already used for ``--pace``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from clickcast.cli import app

runner = CliRunner()


def _capture_do_auto_kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Same shape as tests/test_cli_auto_pace.py — patch _do_auto and expose
    the kwargs it would have received so we can assert on the composite's
    downstream flag values without spinning up Playwright."""
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

    monkeypatch.setattr("clickcast.cli._do_auto", _capture)
    return captured


class TestForHumansFlipsDefaults:
    def test_flips_all_expected_subflags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(
            app,
            [
                "auto",
                "data:text/html,x",
                "--for-humans",
                "--out",
                str(tmp_path / "x.gif"),
            ],
        )
        assert r.exit_code == 0, r.output
        # --pace onboarding → fps 8, dwell 1.2
        assert captured["fps"] == 8
        assert captured["dwell"] == pytest.approx(1.2)
        # --zoom-on-click 2.5 → forwarded as zoom_on_click_factor
        assert captured["zoom_on_click_factor"] == pytest.approx(2.5)
        # Track A/E flips
        assert captured["target_highlight"] is True
        assert captured["title_card"] is True
        assert captured["summary_card"] is True

    def test_without_for_humans_all_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(
            app,
            ["auto", "data:text/html,x", "--out", str(tmp_path / "x.gif")],
        )
        assert r.exit_code == 0, r.output
        # Default pace = natural → fps 12, dwell 0.4
        assert captured["fps"] == 12
        assert captured["dwell"] == pytest.approx(0.4)
        # Track A/E defaults are all False; zoom disabled
        assert captured["zoom_on_click_factor"] is None
        assert captured["target_highlight"] is False
        assert captured["title_card"] is False
        assert captured["summary_card"] is False


class TestForHumansRespectsExplicitOverrides:
    def test_explicit_pace_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(
            app,
            [
                "auto",
                "data:text/html,x",
                "--for-humans",
                "--pace",
                "fast",
                "--out",
                str(tmp_path / "x.gif"),
            ],
        )
        assert r.exit_code == 0, r.output
        # --pace fast → fps 15, dwell 0.15
        assert captured["fps"] == 15
        assert captured["dwell"] == pytest.approx(0.15)
        # Track A/E flips still apply.
        assert captured["target_highlight"] is True

    def test_explicit_zoom_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(
            app,
            [
                "auto",
                "data:text/html,x",
                "--for-humans",
                "--zoom-on-click",
                "1.5",
                "--out",
                str(tmp_path / "x.gif"),
            ],
        )
        assert r.exit_code == 0, r.output
        # 1.5 > 1.0, so it's forwarded as 1.5 (not the 2.5 preset).
        assert captured["zoom_on_click_factor"] == pytest.approx(1.5)


class TestSkillMentionsForHumans:
    """Drift-guard: the ``clickcast skill`` brief must call out --for-humans."""

    def test_brief_lists_for_humans_flag(self) -> None:
        from clickcast.skill import COMMAND_BRIEFS

        auto = next(c for c in COMMAND_BRIEFS if c.name == "auto")
        flag_names = [f.flag for f in auto.key_flags]
        assert any("--for-humans" in f for f in flag_names), (
            "clickcast skill's `auto` brief must document --for-humans"
        )
