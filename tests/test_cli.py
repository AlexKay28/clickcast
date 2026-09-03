from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from clickcast import __version__
from clickcast.cli import (
    _find_playwright_engine,
    _parse_viewport,
    _run_doctor_checks,
    _setup_logging,
    app,
)

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
# _find_playwright_engine — issue #173: must resolve real executables,
# not just hand back the version-cache directory.
# ------------------------------------------------------------------


class TestFindPlaywrightEngine:
    """Regression coverage for #173.

    The old implementation returned e.g. ``~/.cache/ms-playwright/chromium-1092``
    and labeled it "executable path" — but that's a directory. These tests
    build a fake ms-playwright cache under ``tmp_path``, redirect the lookup
    via ``Path.home``, and assert we resolve to the actual binary file.
    """

    def _touch(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    def test_chromium_resolves_to_linux_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("clickcast.cli.sys.platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        # Build both x64 and arm64 layouts so the platform-agnostic lookup is
        # unambiguous — either "chrome-linux" or "chrome-linux64" is correct
        # depending on arch. We ship the CFT x64 layout which is what the
        # candidate list tries first on linux.
        exe = tmp_path / ".cache" / "ms-playwright" / "chromium-1234" / "chrome-linux64" / "chrome"
        self._touch(exe)
        found = _find_playwright_engine("chromium")
        assert found is not None
        path, kind = found
        assert path == exe
        assert path.is_file()
        assert kind == "executable"

    def test_chromium_falls_back_to_linux_arm64_layout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("clickcast.cli.sys.platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        exe = tmp_path / ".cache" / "ms-playwright" / "chromium-1234" / "chrome-linux" / "chrome"
        self._touch(exe)
        found = _find_playwright_engine("chromium")
        assert found is not None
        path, kind = found
        assert path == exe
        assert kind == "executable"

    def test_firefox_resolves_to_linux_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("clickcast.cli.sys.platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        exe = tmp_path / ".cache" / "ms-playwright" / "firefox-9999" / "firefox" / "firefox"
        self._touch(exe)
        found = _find_playwright_engine("firefox")
        assert found is not None
        path, kind = found
        assert path == exe
        assert path.is_file()
        assert kind == "executable"

    def test_webkit_resolves_to_pw_run_launcher(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("clickcast.cli.sys.platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        exe = tmp_path / ".cache" / "ms-playwright" / "webkit-2000" / "pw_run.sh"
        self._touch(exe)
        found = _find_playwright_engine("webkit")
        assert found is not None
        path, kind = found
        assert path == exe
        assert path.is_file()
        assert kind == "executable"

    def test_missing_cache_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("clickcast.cli.sys.platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert _find_playwright_engine("chromium") is None

    def test_novel_layout_falls_back_to_install_dir_with_clear_label(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A future Playwright release ships an install dir but none of the
        # known executable sub-paths exist. Doctor must not lie: report the
        # install dir and clearly label it as such.
        monkeypatch.setattr("clickcast.cli.sys.platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        install = tmp_path / ".cache" / "ms-playwright" / "chromium-9999"
        install.mkdir(parents=True)
        (install / "some-new-layout.txt").write_bytes(b"")
        found = _find_playwright_engine("chromium")
        assert found is not None
        path, kind = found
        assert path == install
        assert kind == "install dir"

    def test_chromium_does_not_match_headless_shell_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `chromium-headless-shell-*` shares the "chromium" prefix but has a
        # different executable layout. The old glob would swallow it and then
        # fail to find `chrome-linux/chrome` inside. Ensure our lookup only
        # considers real `chromium-<version>` install dirs.
        monkeypatch.setattr("clickcast.cli.sys.platform", "linux")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        cache = tmp_path / ".cache" / "ms-playwright"
        (cache / "chromium-headless-shell-1500").mkdir(parents=True)
        exe = cache / "chromium-1000" / "chrome-linux64" / "chrome"
        self._touch(exe)
        found = _find_playwright_engine("chromium")
        assert found is not None
        path, kind = found
        assert path == exe
        assert kind == "executable"


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
        # #196/#199: `elements --json` gains an additive `accessibility`
        # block (role/name/state/grid_cell) alongside the pre-existing
        # heuristic fields.
        assert set(data[0].keys()) == {
            "selector",
            "role",
            "text",
            "bbox",
            "score",
            "source",
            "accessibility",
        }
        a11y = data[0]["accessibility"]
        assert set(a11y.keys()) == {
            "selector",
            "bbox",
            "score",
            "role",
            "name",
            "state",
            "grid_cell",
        }
        assert a11y["grid_cell"] is None  # no --grid flag passed

    def test_grid_flag_populates_grid_cell(self) -> None:
        r = runner.invoke(
            app,
            [
                "elements",
                _FIXTURE_URL,
                "--json",
                "--viewport",
                "400x300",
                "--limit",
                "5",
                "--grid",
                "--grid-pitch",
                "50",
            ],
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.stdout)
        assert data
        for entry in data:
            cell = entry["accessibility"]["grid_cell"]
            assert cell is not None
            assert cell == [entry["bbox"][0] // 50, entry["bbox"][1] // 50]


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


# ------------------------------------------------------------------
# #178: `shot` / `elements` symmetry with `auto` / `run`
# for --headful / --lang / --slowmo / --verbose (+ elements' --device / --dark).
# ------------------------------------------------------------------


class TestShotSymmetryFlags:
    """`shot` must accept --headful/--lang/--slowmo/--verbose and thread
    them through to `_session_kwargs` (issue #178)."""

    def test_all_flags_reach_session_kwargs(self, tmp_path: Path) -> None:
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
                    "--headful",
                    "--slowmo",
                    "100",
                    "--lang",
                    "fr-FR",
                    "--verbose",
                ],
            )
        assert r.exit_code == 0, r.output
        sk = captured["session_kwargs"]  # type: ignore[index]
        assert sk["headful"] is True  # type: ignore[index]
        assert sk["slowmo"] == 100  # type: ignore[index]
        assert sk["lang"] == "fr-FR"  # type: ignore[index]

    def test_verbose_sets_clickcast_logger_level(self, tmp_path: Path) -> None:
        """--verbose on `shot` must bump the clickcast logger like `auto` does."""
        cc = logging.getLogger("clickcast")
        saved = cc.level
        try:

            async def _noop(**_kwargs: object) -> None:
                return None

            with patch("clickcast.cli._do_shot", side_effect=_noop):
                r = runner.invoke(
                    app,
                    [
                        "shot",
                        "data:text/html,x",
                        "--out",
                        str(tmp_path / "s.png"),
                        "-vv",
                    ],
                )
            assert r.exit_code == 0, r.output
            assert cc.level == logging.DEBUG
        finally:
            cc.setLevel(saved)

    def test_defaults_when_no_flags(self, tmp_path: Path) -> None:
        """Sanity: bare `shot` still yields the pre-#178 default session_kwargs."""
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> None:
            captured.update(kwargs)

        with patch("clickcast.cli._do_shot", side_effect=_capture):
            r = runner.invoke(
                app,
                ["shot", "data:text/html,x", "--out", str(tmp_path / "s.png")],
            )
        assert r.exit_code == 0, r.output
        sk = captured["session_kwargs"]  # type: ignore[index]
        assert sk["headful"] is False  # type: ignore[index]
        assert sk["slowmo"] == 0  # type: ignore[index]
        assert sk["lang"] is None  # type: ignore[index]
        assert sk["dark"] is False  # type: ignore[index]


class TestElementsSymmetryFlags:
    """`elements` must accept --device/--headful/--lang/--dark/--slowmo/--verbose
    (issue #178). Previously all six were unavailable."""

    def test_all_flags_reach_session_kwargs(self) -> None:
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> tuple[list[object], list[object]]:
            captured.update(kwargs)
            return [], []

        with patch("clickcast.cli._do_elements", side_effect=_capture):
            r = runner.invoke(
                app,
                [
                    "elements",
                    "data:text/html,x",
                    "--device",
                    "iPhne 15",  # any string — device is a bare passthrough
                    "--headful",
                    "--dark",
                    "--slowmo",
                    "50",
                    "--lang",
                    "en-US",
                    "--verbose",
                ],
            )
        # Typer accepts unknown device strings; the CLI just forwards them.
        assert r.exit_code == 0, r.output
        sk = captured["session_kwargs"]  # type: ignore[index]
        assert sk["device"] == "iPhne 15"  # type: ignore[index]
        assert sk["headful"] is True  # type: ignore[index]
        assert sk["dark"] is True  # type: ignore[index]
        assert sk["slowmo"] == 50  # type: ignore[index]
        assert sk["lang"] == "en-US"  # type: ignore[index]

    def test_verbose_sets_clickcast_logger_level(self) -> None:
        """--verbose on `elements` must bump the clickcast logger."""
        cc = logging.getLogger("clickcast")
        saved = cc.level
        try:

            async def _noop(**_kwargs: object) -> tuple[list[object], list[object]]:
                return [], []

            with patch("clickcast.cli._do_elements", side_effect=_noop):
                r = runner.invoke(
                    app,
                    ["elements", "data:text/html,x", "-vv"],
                )
            assert r.exit_code == 0, r.output
            assert cc.level == logging.DEBUG
        finally:
            cc.setLevel(saved)

    def test_defaults_when_no_flags(self) -> None:
        """Sanity: bare `elements` still yields the pre-#178 defaults."""
        captured: dict[str, object] = {}

        async def _capture(**kwargs: object) -> tuple[list[object], list[object]]:
            captured.update(kwargs)
            return [], []

        with patch("clickcast.cli._do_elements", side_effect=_capture):
            r = runner.invoke(app, ["elements", "data:text/html,x"])
        assert r.exit_code == 0, r.output
        sk = captured["session_kwargs"]  # type: ignore[index]
        assert sk["device"] is None  # type: ignore[index]
        assert sk["headful"] is False  # type: ignore[index]
        assert sk["lang"] is None  # type: ignore[index]
        assert sk["dark"] is False  # type: ignore[index]
        assert sk["slowmo"] == 0  # type: ignore[index]
