from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

import clickcast.cli as cli
from clickcast.core.actions import ActionResult, ClickStep
from clickcast.discovery import Element
from clickcast.scenario import RunResult, Scenario, loads

runner = CliRunner()


@pytest.mark.asyncio
async def test_discovery_scenario_quotes_untrusted_scalars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = 'Open\n  - evaluate: "return 1"\n    label: injected'
    selector = 'button[title="a\\b"c"]'

    class FakeSession:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def goto(self, *_args: object, **_kwargs: object) -> None:
            return None

    element = Element(
        selector=selector,
        role="button",
        text=label,
        bbox=(0, 0, 10, 10),
        score=1,
        source="dom-heuristic",
    )
    monkeypatch.setattr(cli, "Session", FakeSession)
    monkeypatch.setattr(cli, "discover", AsyncMock(return_value=[element]))

    content = await cli._scenario_from_discovery("https://example.test", "Review test", "reel.gif")
    scenario = loads(content)

    assert content.startswith("meta:\n")
    assert "steps:\n" in content
    assert [step.action for step in scenario.steps] == ["goto", "click"]
    assert scenario.steps[1].selector == selector
    assert scenario.steps[1].label == label


@pytest.mark.parametrize(
    "entry",
    [
        "Bad\rName: value",
        "X-Test: bad\nvalue",
        "X-Test: bad\x00value",
        "\x7f: value",
    ],
)
def test_header_flags_reject_control_characters(entry: str) -> None:
    with pytest.raises(typer.BadParameter, match="control characters"):
        cli._parse_header_flags([entry])


def test_run_configures_verbose_logging(tmp_path: Path) -> None:
    scenario_path = tmp_path / "tour.yml"
    scenario_path.write_text("meta:\n  name: test\nsteps: []\n")

    async def noop(**_kwargs: object) -> None:
        return None

    clickcast_logger = logging.getLogger("clickcast")
    saved_level = clickcast_logger.level
    try:
        with (
            patch.object(cli, "_do_run", side_effect=noop),
            patch.object(cli, "_setup_logging", wraps=cli._setup_logging) as setup_logging,
        ):
            result = runner.invoke(cli.app, ["run", str(scenario_path), "-vv"])

        assert result.exit_code == 0, result.output
        setup_logging.assert_called_once_with(2)
        assert clickcast_logger.level == logging.DEBUG
    finally:
        clickcast_logger.setLevel(saved_level)


@pytest.mark.parametrize("wait", ["nan", "inf", "-inf"])
def test_shot_rejects_non_finite_wait(wait: str) -> None:
    result = runner.invoke(cli.app, ["shot", "https://example.test", "--wait", wait])

    assert result.exit_code == 2, result.output
    assert "numeric seconds must be finite" in result.output


@pytest.mark.asyncio
async def test_run_failure_stderr_uses_sidecar_redaction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = Scenario(steps=[ClickStep(selector="#missing")])
    secret = "secret-token"
    error = (
        f"step 1: request https://example.test/callback?code={secret}&next=/home "
        f"failed with {secret}"
    )
    run_result = RunResult(
        results=[
            ActionResult(
                ok=False,
                status="failed",
                action="click",
                selector="#missing",
                error=error,
            )
        ],
        failed_at=0,
    )

    class FakeRecorder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.frames_dir = tmp_path / "frames"
            self.frames_dir.mkdir()

        def __enter__(self) -> FakeRecorder:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def flush(self) -> list[object]:
            return []

    encoded = MagicMock()
    encoded.path = tmp_path / "reel.gif"
    encoded.format = "gif"
    encoded.size_bytes = 1024
    encoded.frame_count = 1
    encoded.duration_s = 1.0

    with (
        patch.object(cli, "run_scenario", AsyncMock(return_value=run_result)),
        patch.object(cli, "Recorder", FakeRecorder),
        patch.object(cli, "annotate_frames_dir"),
        patch.object(cli, "encode", return_value=encoded),
        pytest.raises(typer.Exit) as raised,
    ):
        await cli._do_run(
            scenario=scenario,
            out=str(encoded.path),
            format_=None,
            no_sidecar=True,
            redact_patterns=[re.compile(r"secret-[A-Za-z0-9]+")],
            strip_query_strings=True,
        )

    assert raised.value.exit_code == 1
    stderr = capsys.readouterr().err
    assert secret not in stderr
    assert "?code=" not in stderr
    assert "https://example.test/callback failed with «redacted»" in stderr


def test_cli_logger_has_cli_name() -> None:
    assert cli.log.name == "clickcast.cli"


def test_doctor_config_dir_is_informational_but_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cli, "user_config_dir", lambda _name: str(config_dir))

    missing_report = cli._run_doctor_checks()
    missing = next(check for check in missing_report["checks"] if check["name"] == "config-dir")
    assert missing["ok"] is False
    assert missing["required"] is False

    config_dir.mkdir()
    present_report = cli._run_doctor_checks()
    present = next(check for check in present_report["checks"] if check["name"] == "config-dir")
    assert present["ok"] is True
    assert present["required"] is False
