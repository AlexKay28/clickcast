"""Unit tests for `scripts/bench_metrics.py` (#258).

Only the pure pieces are exercised here — the measurement path itself spawns a
fresh venv, downloads wheels and launches a browser, which belongs in a manual
or scheduled run, never in the unit suite.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_metrics.py"

pytestmark = pytest.mark.unit


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_metrics", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


def test_estimate_tokens_rounds_up_at_four_chars_per_token() -> None:
    assert bench.estimate_tokens(0) == 0
    assert bench.estimate_tokens(1) == 1
    assert bench.estimate_tokens(4) == 1
    assert bench.estimate_tokens(5) == 2
    assert bench.estimate_tokens(1600) == 400


def test_percentile_is_nearest_rank_and_handles_empty() -> None:
    assert bench.percentile([], 50) == 0.0
    assert bench.percentile([9.0], 95) == 9.0
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert bench.percentile(values, 50) == 3.0
    assert bench.percentile(values, 95) == 5.0
    assert bench.percentile(values, 0) == 1.0


def test_bytes_per_step_is_per_recorded_step_not_per_max_steps() -> None:
    """The first baseline got this wrong: `--max-steps 2` records 4 steps
    (goto + clicks + scroll), so dividing by max-steps doubled the number."""
    run = bench.Run(sidecar_bytes=6040, sidecar_chars=6040, steps=4)
    assert run.bytes_per_step == 1510
    assert run.estimated_tokens_per_step == math.ceil(6040 / 4) / 4


def test_bytes_per_step_is_zero_without_steps() -> None:
    assert bench.Run(sidecar_bytes=10, steps=0).bytes_per_step == 0.0
    assert bench.Run(sidecar_bytes=10, steps=0).estimated_tokens_per_step == 0.0


def _measurement(runs: list[object]) -> ModuleType:
    measurement = bench.Measurement(venv_s=3.0, install_s=19.0, doctor_s=0.7, doctor_ok=True)
    measurement.runs = runs
    return measurement


def test_total_is_install_plus_first_tour() -> None:
    first = bench.Run(run_s=9.5, sidecar_bytes=6040, sidecar_chars=6040, steps=4)
    second = bench.Run(run_s=99.0, sidecar_bytes=6040, sidecar_chars=6040, steps=4)
    assert _measurement([first, second]).total_s == pytest.approx(3.0 + 19.0 + 0.7 + 9.5)


def test_engine_install_is_reported_separately_from_m1() -> None:
    measurement = _measurement([bench.Run(run_s=9.0, sidecar_bytes=4000, steps=4)])
    measurement.engine_install_s = 180.0
    summary = bench.summarize(measurement)
    assert summary["m1"]["engine_install_s"] == 180.0
    assert summary["m1"]["total_s"] == pytest.approx(31.7)


def test_summarize_publishes_both_headline_numbers() -> None:
    measurement = _measurement(
        [bench.Run(run_s=9.0, sidecar_bytes=6040, sidecar_chars=6040, steps=4)]
    )
    summary = bench.summarize(measurement)
    assert summary["m1"]["doctor_ok"] is True
    assert summary["m2"]["bytes_per_step_p50"] == 1510
    assert summary["m2"]["estimated_tokens_per_step_p50"] == 378
    markdown = bench.render_markdown(summary)
    assert "M1: install → verified report" in markdown
    assert "M2: estimated tokens/step" in markdown


def test_ceilings_flag_a_regression_and_pass_a_baseline() -> None:
    measurement = _measurement(
        [bench.Run(run_s=9.0, sidecar_bytes=6040, sidecar_chars=6040, steps=4)]
    )
    assert bench.check_ceilings(bench.summarize(measurement)) == []

    slow = _measurement([bench.Run(run_s=300.0, sidecar_bytes=60_000, steps=4)])
    failures = bench.check_ceilings(bench.summarize(slow))
    assert len(failures) == 2


def test_cold_pip_runs_are_not_judged_against_the_warm_ceiling() -> None:
    measurement = _measurement([bench.Run(run_s=300.0, sidecar_bytes=4000, steps=4)])
    measurement.pip_cold = True
    assert bench.check_ceilings(bench.summarize(measurement)) == []


def test_cli_defaults_are_reproducible() -> None:
    args = bench.parse_args([])
    assert args.repeat == 1
    assert args.max_steps == 2
    assert args.engine == "chromium"
    assert args.page == "index.html"
    assert args.cache is True
    assert bench.FIXTURE_DIR.joinpath(args.page).exists()


def test_cli_no_cache_dir_is_opt_in() -> None:
    assert bench.parse_args(["--no-cache-dir"]).cache is False
