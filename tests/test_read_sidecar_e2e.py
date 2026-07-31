"""End-to-end coverage for the AI-consumer script.

`tests/test_fixture_site.py` proves a real-pipeline sidecar validates
against the schema; `tests/test_feedback.py::TestConsumerExample` proves
`tests/consumer/read_sidecar.py` can parse a **hand-built** report. Neither
chains the real pipeline through the consumer, so a silent format drift
between what the pipeline writes and what agents consume would slip past
both. This module closes that loop (#99):

- Happy-path Reel run -> real sidecar on disk -> subprocess invocation
  of `read_sidecar.py` -> assert clean exit.
- Failed-step Reel run -> real sidecar containing status != "ok" ->
  subprocess invocation -> assert the failed step surfaces in stdout.

Marked ``integration`` — chromium required, same as sibling fixture-site
tests. The subprocess call intentionally uses `sys.executable` and runs
from the repo root so it exercises the script exactly the way a
downstream agent would invoke it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from clickcast import Reel

REPO_ROOT = Path(__file__).parent.parent
CONSUMER_SCRIPT = REPO_ROOT / "tests" / "consumer" / "read_sidecar.py"


def _run_consumer(sidecar: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the consumer script the way a downstream agent would.

    `check=False` because the caller inspects `returncode` explicitly —
    a non-zero exit is a test failure with a helpful assert message, not
    a raised CalledProcessError swallowing the stderr context.
    """
    return subprocess.run(
        [sys.executable, str(CONSUMER_SCRIPT), str(sidecar)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.integration
class TestReadSidecarE2E:
    def test_happy_path_pipeline_produces_sidecar_the_consumer_accepts(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        """A clean Reel run yields a sidecar that `read_sidecar.py` parses
        without error. The current consumer only prints failed steps
        (see follow-up note in the PR body), so on a happy pipeline
        stdout is legitimately empty — the assertion is "exit 0, no
        parse error surfaced on stderr".
        """
        out = tmp_path / "tour.gif"
        Reel(fixture_site_url, viewport=(600, 400), fps=4, dwell=0.25).goto(wait="load").click(
            "#btn-3d", label="Click 3D", dwell=0.25
        ).save(out)

        sidecar = out.with_suffix(out.suffix + ".json")
        assert sidecar.exists(), "pipeline must emit sidecar next to media"
        # Sanity: the real sidecar has the click step we asked for, ok.
        payload = json.loads(sidecar.read_text())
        actions = [s["action"] for s in payload["steps"]]
        assert actions == ["goto", "click"]
        assert all(s["status"] == "ok" for s in payload["steps"])

        result = _run_consumer(sidecar)
        assert result.returncode == 0, (
            f"consumer failed on a valid happy-path sidecar\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        # No parser complaints on stderr — a "missing steps" or JSON
        # decode error would fail here even if the exit code drifted.
        assert result.stderr == "", f"consumer produced unexpected stderr: {result.stderr!r}"

    def test_failed_click_surfaces_in_consumer_stdout(
        self, fixture_site_url: str, tmp_path: Path
    ) -> None:
        """A Reel run with a broken selector produces a sidecar containing
        a failed step, and `read_sidecar.py` surfaces that step on stdout.

        The failing click stops the run (optional defaults to False), so
        Reel.save still writes the sidecar with the partial results — the
        pipeline's exit-code contract is separate from what the consumer
        reads. Assertions are tolerant of the exact stdout format: the
        script prints `"<index> <action> -> <frames>"` per failed step,
        so we check for the action substring rather than the whole line.
        """
        out = tmp_path / "broken.gif"
        Reel(fixture_site_url, viewport=(600, 400), fps=4, dwell=0.25).goto(wait="load").click(
            "#definitely-does-not-exist", label="Doomed click", dwell=0.1
        ).save(out)

        sidecar = out.with_suffix(out.suffix + ".json")
        assert sidecar.exists(), (
            "sidecar must be written even when a step fails — that's the "
            "whole point of the feedback loop"
        )
        payload = json.loads(sidecar.read_text())
        failed_steps = [s for s in payload["steps"] if s.get("status") != "ok"]
        assert len(failed_steps) >= 1, (
            f"expected at least one failed step, got statuses: "
            f"{[s.get('status') for s in payload['steps']]}"
        )

        result = _run_consumer(sidecar)
        assert result.returncode == 0, (
            f"consumer must exit 0 even when it reports failures\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )
        assert result.stdout.strip() != "", (
            "consumer stdout must surface the failed step; got empty output"
        )
        # Tolerant assertion: the action name of the failed step appears
        # somewhere in stdout. The current format is "<idx> click -> ..."
        # so this survives whitespace/format tweaks as long as the action
        # keeps appearing on the failure line.
        assert "click" in result.stdout, (
            f"consumer stdout must mention the failed action; got: {result.stdout!r}"
        )
