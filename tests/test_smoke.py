from typer.testing import CliRunner

import clickcast
from clickcast.cli import app

runner = CliRunner()


def test_import_exposes_version() -> None:
    assert isinstance(clickcast.__version__, str)
    assert clickcast.__version__


def test_cli_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "clickcast" in result.stdout.lower()


def test_cli_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert clickcast.__version__ in result.stdout


def test_config_path_command_runs() -> None:
    # `config path` is deliberately implementation-free at MVP (it only needs
    # platformdirs) — a good replacement for the obsolete "stub exits 2" smoke.
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0
    assert "config.toml" in result.stdout
