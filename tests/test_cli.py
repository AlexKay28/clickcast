from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clickcast import __version__
from clickcast.cli import _parse_viewport, _run_doctor_checks, _setup_logging, app

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

    def test_ignores_system_playwright_binary(self) -> None:
        """Regression for #176: a system-wide `playwright` on PATH must not
        override the venv's playwright module — otherwise `install` fetches
        browsers for the wrong playwright version.
        """
        with (
            patch("shutil.which", return_value="/usr/local/bin/playwright") as which,
            patch("clickcast.cli.subprocess.run") as sp,
        ):
            sp.return_value.returncode = 0
            r = runner.invoke(app, ["install", "chromium"])
        assert r.exit_code == 0
        argv = sp.call_args.args[0]
        assert argv == [sys.executable, "-m", "playwright", "install", "chromium"]
        assert not argv[0].endswith("/playwright")
        # The fix should not consult shutil.which at all for the invocation,
        # but tolerate the case where it's called elsewhere — assert the
        # invocation is correct regardless of what which() would return.
        _ = which  # silence unused-var lint


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


# ------------------------------------------------------------------
# #174 — _setup_logging must not wipe root handlers of a library caller
# ------------------------------------------------------------------


class TestSetupLoggingScoping:
    """Regression tests for #174.

    Pre-fix, ``_setup_logging`` called ``logging.basicConfig(force=True)``,
    which silently detaches every handler already attached to the root
    logger. Apps that import clickcast as a library (with their own JSON /
    structured / Sentry root handlers) lost them the instant any code path
    called ``_setup_logging``.

    The fix scopes ``_setup_logging`` to the ``"clickcast"`` logger only,
    and installs a stderr handler on root exclusively from :func:`main`
    (the CLI entrypoint) when root has no handlers yet.
    """

    def _reset_clickcast_logger(self) -> None:
        cc = logging.getLogger("clickcast")
        cc.setLevel(logging.NOTSET)

    def test_library_mode_preserves_root_handlers(self) -> None:
        """Direct call to ``_setup_logging`` (library mode) must not detach
        pre-existing root handlers. Also asserts the ``clickcast`` logger
        reflects the requested verbosity."""
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        try:
            root.handlers.clear()
            sentinel = logging.NullHandler()
            root.addHandler(sentinel)

            _setup_logging(1)

            assert sentinel in root.handlers, (
                "root handler was detached — _setup_logging leaked into root logging"
            )
            assert logging.getLogger("clickcast").level == logging.INFO

            _setup_logging(2)
            assert sentinel in root.handlers
            assert logging.getLogger("clickcast").level == logging.DEBUG
        finally:
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)
            self._reset_clickcast_logger()

    def test_cli_mode_sets_clickcast_level(self) -> None:
        """Invoking a CLI command with ``--verbose`` must still bump the
        ``clickcast`` logger's effective level (INFO for one -v, DEBUG for
        two)."""
        cc = logging.getLogger("clickcast")
        saved_cc_level = cc.level
        try:
            # Patch _do_auto so no real browser launches — the coroutine
            # it returns is closed to avoid a RuntimeWarning about a never-
            # awaited coroutine when the caller sees our async stub.
            async def _noop(**_kwargs: object) -> None:
                return None

            with patch("clickcast.cli._do_auto", side_effect=_noop):
                r = runner.invoke(
                    app,
                    [
                        "auto",
                        "https://example.com",
                        "--verbose",
                        "--max-steps",
                        "1",
                    ],
                )
            assert r.exit_code == 0, r.output
            assert cc.level == logging.INFO

            with patch("clickcast.cli._do_auto", side_effect=_noop):
                r = runner.invoke(
                    app,
                    [
                        "auto",
                        "https://example.com",
                        "-vv",
                        "--max-steps",
                        "1",
                    ],
                )
            assert r.exit_code == 0, r.output
            assert cc.level == logging.DEBUG
        finally:
            cc.setLevel(saved_cc_level)
