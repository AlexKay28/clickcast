"""Exercise every example scenario shipped in ``docs/scenarios/`` end-to-end.

Each scenario is loaded via the CLI (`clickcast run`), pointed at the
fixture-site URL via ``--var base_url=...``, and asserted to produce a
valid GIF + a `schema_version: 1` sidecar. This gives us:

- A regression against the roadmap acceptance for #15: every example
  scenario executes end-to-end.
- A regression against the roadmap acceptance for #14: no test hits the
  public internet.
- A regression against #12: every documented example produces the
  schema an AI consumer relies on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from clickcast.cli import app

runner = CliRunner()

REPO_ROOT = Path(__file__).parent.parent
SCENARIO_DIR = REPO_ROOT / "docs" / "scenarios"


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_name",
    ["spa.yml", "form.yml", "tabs.yml"],
    ids=["spa", "form", "tabs"],
)
def test_docs_scenario_runs_end_to_end(
    scenario_name: str,
    fixture_site_url: str,
    tmp_path: Path,
) -> None:
    scenario = SCENARIO_DIR / scenario_name
    assert scenario.exists(), f"scenario missing: {scenario}"

    out = tmp_path / f"{scenario_name}.gif"
    result = runner.invoke(
        app,
        [
            "run",
            str(scenario),
            "--out",
            str(out),
            "--var",
            f"base_url={fixture_site_url}",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()

    # A real GIF with more than one frame — otherwise the whole reel
    # collapsed to a single frame (the pathology we fixed in the palette PR).
    with Image.open(out) as img:
        assert img.n_frames >= 2

    # Sidecar validates against the schema #12 promises to downstream AI.
    sidecar = out.with_suffix(out.suffix + ".json")
    payload = json.loads(sidecar.read_text())
    assert payload["schema_version"] == 1
    assert payload["media"]["format"] == "gif"
    assert payload["media"]["frame_count"] >= 2
    assert len(payload["steps"]) >= 1
    # Every step in a shipped example scenario should end status="ok".
    non_ok = [s for s in payload["steps"] if s["status"] != "ok"]
    assert not non_ok, f"scenario had non-ok steps: {non_ok}"
