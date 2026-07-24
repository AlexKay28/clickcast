"""`--pace` preset behavior for `clickcast auto`.

Ships #76. `--pace={fast,natural,slow,onboarding}` sets fps + dwell together
so users don't have to think about frame math. Explicit `--fps` / `--dwell`
still override.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clickcast.cli import app

runner = CliRunner()


def _capture_do_auto_kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch `_do_auto` + config loader so we can invoke `auto` and inspect what
    would have been passed downstream, without actually running Playwright."""
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


class TestPacePresets:
    @pytest.mark.parametrize(
        ("pace", "expected_fps", "expected_dwell"),
        [
            ("fast", 15, 0.15),
            ("natural", 12, 0.4),
            ("slow", 10, 0.7),
            ("onboarding", 8, 1.2),
        ],
    )
    def test_pace_sets_fps_and_dwell(
        self,
        pace: str,
        expected_fps: int,
        expected_dwell: float,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(
            app,
            ["auto", "data:text/html,x", "--pace", pace, "--out", str(tmp_path / "x.gif")],
        )
        assert r.exit_code == 0, r.output
        assert captured["fps"] == expected_fps, (
            f"pace={pace}: expected fps={expected_fps}, got {captured['fps']}"
        )
        assert captured["dwell"] == pytest.approx(expected_dwell), (
            f"pace={pace}: expected dwell={expected_dwell}, got {captured['dwell']}"
        )

    def test_explicit_fps_beats_pace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """User explicitly setting --fps must override the pace preset's fps
        (but not the pace preset's dwell)."""
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(
            app,
            [
                "auto",
                "data:text/html,x",
                "--pace",
                "onboarding",
                "--fps",
                "24",
                "--out",
                str(tmp_path / "x.gif"),
            ],
        )
        assert r.exit_code == 0, r.output
        assert captured["fps"] == 24, f"explicit --fps must win, got {captured['fps']}"
        assert captured["dwell"] == pytest.approx(1.2)

    def test_explicit_dwell_beats_pace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(
            app,
            [
                "auto",
                "data:text/html,x",
                "--pace",
                "fast",
                "--dwell",
                "2.0",
                "--out",
                str(tmp_path / "x.gif"),
            ],
        )
        assert r.exit_code == 0, r.output
        assert captured["dwell"] == pytest.approx(2.0)
        assert captured["fps"] == 15

    def test_invalid_pace_dies(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(
            app,
            [
                "auto",
                "data:text/html,x",
                "--pace",
                "extreme",
                "--out",
                str(tmp_path / "x.gif"),
            ],
        )
        assert r.exit_code != 0
        assert "pace" in r.output.lower()

    def test_default_pace_is_natural(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        with patch("clickcast.cli._is_explicit", return_value=False):
            r = runner.invoke(app, ["auto", "data:text/html,x", "--out", str(tmp_path / "x.gif")])
        assert r.exit_code == 0, r.output
        assert captured["fps"] == 12
        assert captured["dwell"] == pytest.approx(0.4)


class TestConfigLayerPace:
    def test_pace_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`CLICKCAST_PACE=slow` should flow through Config → CLI defaults →
        the pace preset resolution."""
        monkeypatch.setenv("CLICKCAST_PACE", "slow")
        captured = _capture_do_auto_kwargs(tmp_path, monkeypatch)
        r = runner.invoke(app, ["auto", "data:text/html,x", "--out", str(tmp_path / "x.gif")])
        assert r.exit_code == 0, r.output
        assert captured["fps"] == 10
        assert captured["dwell"] == pytest.approx(0.7)
