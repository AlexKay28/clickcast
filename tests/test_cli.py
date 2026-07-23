from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clickcast import __version__
from clickcast.cli import _parse_viewport, _run_doctor_checks, app

runner = CliRunner()


# ------------------------------------------------------------------
# Top-level / smoke
# ------------------------------------------------------------------


class TestTopLevel:
    def test_version(self) -> None:
        r = runner.invoke(app, ["--version"])
        assert r.exit_code == 0
        assert __version__ in r.stdout

    def test_help_lists_all_commands(self) -> None:
        r = runner.invoke(app, ["--help"])
        assert r.exit_code == 0
        for cmd in (
            "auto",
            "run",
            "shot",
            "init",
            "elements",
            "doctor",
            "config",
            "install",
        ):
            assert cmd in r.stdout

    def test_no_args_shows_help(self) -> None:
        r = runner.invoke(app, [])
        # no_args_is_help=True means we exit 2 and print help
        assert r.exit_code == 2


class TestParseViewport:
    def test_valid(self) -> None:
        assert _parse_viewport("1280x800") == (1280, 800)
        assert _parse_viewport("1280X800") == (1280, 800)

    def test_invalid_raises(self) -> None:
        with pytest.raises(Exception, match="viewport"):
            _parse_viewport("bogus")


# ------------------------------------------------------------------
# init — no browser needed unless --from-auto
# ------------------------------------------------------------------


class TestInit:
    def test_writes_starter_scenario(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.yml"
        r = runner.invoke(app, ["init", str(out), "--url", "https://example.com"])
        assert r.exit_code == 0, r.output
        assert out.exists()
        content = out.read_text()
        assert "meta:" in content
        assert "steps:" in content
        assert "https://example.com" in content

    def test_refuses_to_overwrite_without_force(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.yml"
        out.write_text("existing")
        r = runner.invoke(app, ["init", str(out)])
        assert r.exit_code == 1
        assert out.read_text() == "existing"

    def test_force_overwrites(self, tmp_path: Path) -> None:
        out = tmp_path / "tour.yml"
        out.write_text("old")
        r = runner.invoke(app, ["init", str(out), "--force"])
        assert r.exit_code == 0
        assert "meta:" in out.read_text()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        out = tmp_path / "nested" / "dir" / "tour.yml"
        r = runner.invoke(app, ["init", str(out)])
        assert r.exit_code == 0
        assert out.exists()


# ------------------------------------------------------------------
# config
# ------------------------------------------------------------------


class TestConfig:
    def test_path_prints_platform_path(self) -> None:
        r = runner.invoke(app, ["config", "path"])
        assert r.exit_code == 0
        assert "clickcast" in r.stdout
        assert "config.toml" in r.stdout

    def test_get_returns_effective_default(self) -> None:
        # After #13 shipped, `config get engine` returns the actual value
        # rather than the "requires #13" stub.
        r = runner.invoke(app, ["config", "get", "engine"])
        assert r.exit_code == 0
        assert "chromium" in r.stdout

    def test_unknown_action_rejected(self) -> None:
        r = runner.invoke(app, ["config", "bogus"])
        assert r.exit_code != 0


# ------------------------------------------------------------------
# doctor
# ------------------------------------------------------------------


class TestDoctor:
    def test_returns_report_structure(self) -> None:
        rep = _run_doctor_checks()
        assert "checks" in rep
        assert "ok" in rep
        names = {c["name"] for c in rep["checks"]}
        assert "python" in names
        assert "playwright" in names
        assert "engine.chromium" in names
        assert "ffmpeg" in names
        assert "config-dir" in names

    def test_python_check_passes(self) -> None:
        rep = _run_doctor_checks()
        py = next(c for c in rep["checks"] if c["name"] == "python")
        assert py["ok"] is True

    def test_json_output(self) -> None:
        r = runner.invoke(app, ["doctor", "--json"])
        data = json.loads(r.stdout)
        assert "checks" in data
        assert isinstance(data["checks"], list)


# ------------------------------------------------------------------
# install — just verify we invoke playwright with the right args
# ------------------------------------------------------------------


class TestInstall:
    def test_default_installs_chromium(self) -> None:
        with patch("clickcast.cli.subprocess.run") as sp:
            sp.return_value.returncode = 0
            r = runner.invoke(app, ["install"])
        assert r.exit_code == 0
        argv = sp.call_args.args[0]
        assert "install" in argv
        assert argv[-1] == "chromium"
        assert "--with-deps" not in argv

    def test_with_deps_flag_forwarded(self) -> None:
        with patch("clickcast.cli.subprocess.run") as sp:
            sp.return_value.returncode = 0
            r = runner.invoke(app, ["install", "--with-deps", "firefox", "webkit"])
        assert r.exit_code == 0
        argv = sp.call_args.args[0]
        assert "--with-deps" in argv
        assert argv[-2:] == ["firefox", "webkit"]

    def test_propagates_nonzero_exit(self) -> None:
        with patch("clickcast.cli.subprocess.run") as sp:
            sp.return_value.returncode = 3
            r = runner.invoke(app, ["install"])
        assert r.exit_code == 3


# ------------------------------------------------------------------
# Integration — real chromium against inline HTML
# ------------------------------------------------------------------

_FIXTURE_URL = "data:text/html,<html><body><h1>hi</h1><button>Click me</button></body></html>"


@pytest.mark.integration
class TestShotIntegration:
    def test_writes_png(self, tmp_path: Path) -> None:
        out = tmp_path / "shot.png"
        r = runner.invoke(
            app,
            [
                "shot",
                _FIXTURE_URL,
                "--out",
                str(out),
                "--wait",
                "load",
                "--viewport",
                "400x300",
            ],
        )
        assert r.exit_code == 0, r.output
        assert out.exists()
        assert out.read_bytes().startswith(b"\x89PNG")


@pytest.mark.integration
class TestElementsIntegration:
    def test_json_output_parseable(self) -> None:
        r = runner.invoke(
            app,
            ["elements", _FIXTURE_URL, "--json", "--viewport", "400x300", "--limit", "5"],
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.stdout)
        assert isinstance(data, list)
        assert data  # at least one element
        assert set(data[0].keys()) == {"selector", "role", "text", "bbox", "score", "source"}


@pytest.mark.integration
class TestRunIntegration:
    def test_full_yaml_scenario_produces_gif(self, tmp_path: Path) -> None:
        scenario = tmp_path / "tour.yml"
        out = tmp_path / "tour.gif"
        scenario.write_text(
            f"""
            meta:
              viewport: 400x300
              fps: 4
              dwell: 0.25
              format: gif
              out: {out}
            steps:
              - goto: {_FIXTURE_URL}
                wait: load
                label: Open
            """
        )
        r = runner.invoke(app, ["run", str(scenario)])
        assert r.exit_code == 0, r.output
        assert out.exists()
        assert out.stat().st_size > 500


@pytest.mark.integration
class TestAutoIntegration:
    def test_auto_produces_a_gif(self, tmp_path: Path) -> None:
        out = tmp_path / "auto.gif"
        r = runner.invoke(
            app,
            [
                "auto",
                _FIXTURE_URL,
                "--out",
                str(out),
                "--max-steps",
                "1",
                "--dwell",
                "0.25",
                "--initial-wait",
                "0.25",
                "--viewport",
                "400x300",
                "--fps",
                "4",
            ],
        )
        assert r.exit_code == 0, r.output
        assert out.exists()
        assert out.stat().st_size > 500

    def test_no_sidecar_flag_skips_sidecar(self, tmp_path: Path) -> None:
        out = tmp_path / "auto.gif"
        r = runner.invoke(
            app,
            [
                "auto",
                _FIXTURE_URL,
                "--out",
                str(out),
                "--max-steps",
                "1",
                "--dwell",
                "0.25",
                "--initial-wait",
                "0.25",
                "--viewport",
                "400x300",
                "--fps",
                "4",
                "--no-sidecar",
            ],
        )
        assert r.exit_code == 0, r.output
        assert out.exists()
        sidecar = out.with_suffix(out.suffix + ".json")
        assert not sidecar.exists()
