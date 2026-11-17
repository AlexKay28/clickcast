#!/usr/bin/env python
"""Measure clickcast's two headline numbers, reproducibly.

Headline metrics (see `docs/benchmarks.md`):

* **M1 — time to a verified report**: fresh venv -> `pip install clickcast` ->
  `clickcast doctor --json` -> a real `clickcast auto` tour against a local
  fixture site -> a sidecar that loads through the shipped schema. Browser
  download time is reported separately, never folded into the install number.
* **M2 — agent context cost**: bytes (and estimated tokens, ``chars / 4``) of
  the sidecar per step. The perception API will add `observe()` / delta /
  receipt ceilings on top; today's sidecar is the only machine payload, so it
  is the honest baseline.

Usage::

    python scripts/bench_metrics.py                 # one full run, warm pip cache
    python scripts/bench_metrics.py --repeat 3      # 3 tours on one install
    python scripts/bench_metrics.py --no-cache-dir  # cold pip (network)
    python scripts/bench_metrics.py --reuse-venv    # skip install, re-time tours

The script is dependency-free (stdlib only) so it runs before clickcast is
installed. It exits non-zero when the tour fails, the sidecar does not
validate, or a measurement crosses its generous regression ceiling.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "site"

# Generous regression ceilings — a real regression is a 2x jump, not noise.
WARM_TOTAL_CEILING_S = 180.0
SIDECAR_BYTES_PER_STEP_CEILING = 8_000


@dataclass
class Run:
    """One `auto` tour on an already-installed environment."""

    run_s: float = 0.0
    exit_code: int = 0
    sidecar_bytes: int = 0
    sidecar_chars: int = 0
    steps: int = 0
    schema_version: int = 0
    frames: int = 0
    reel_bytes: int = 0

    @property
    def bytes_per_step(self) -> float:
        return self.sidecar_bytes / self.steps if self.steps else 0.0

    @property
    def estimated_tokens_per_step(self) -> float:
        return estimate_tokens(self.sidecar_chars) / self.steps if self.steps else 0.0


@dataclass
class Measurement:
    install_s: float = 0.0
    engine_install_s: float = 0.0
    doctor_s: float = 0.0
    venv_s: float = 0.0
    pip_cold: bool = False
    doctor_ok: bool = False
    runs: list[Run] = field(default_factory=list)

    @property
    def total_s(self) -> float:
        """Time to first verified report: venv + install + doctor + first tour.

        The engine download is deliberately excluded here and reported
        separately — it dominates a cold machine and is not a clickcast cost.
        """
        first = self.runs[0].run_s if self.runs else 0.0
        return self.venv_s + self.install_s + self.doctor_s + first


def estimate_tokens(chars: int) -> int:
    """Deterministic, tokenizer-free estimate: one token per four characters.

    Model-agnostic on purpose — the point of M2 is a stable budget, not a
    vendor-specific count. Real tokenizers are an optional cross-check.
    """
    return math.ceil(chars / 4)


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile — no numpy, no interpolation surprises."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q / 100 * len(ordered)) - 1))
    return ordered[index]


def _serve(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


def _timed(cmd: list[str], **kwargs: Any) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return proc, time.perf_counter() - started


def _bin(venv: Path, name: str) -> str:
    scripts = venv / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    return str(scripts / f"{name}{suffix}")


def _doctor(venv: Path) -> tuple[dict[str, Any], float]:
    proc, elapsed = _timed([_bin(venv, "clickcast"), "doctor", "--json"])
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = {
            "ok": False,
            "checks": [],
            "raw": proc.stdout[:2000],
            "stderr": proc.stderr[-2000:],
        }
    return report, elapsed


def _engine_ready(report: dict[str, Any], engine: str) -> bool:
    for check in report.get("checks", []):
        if check.get("name") == f"engine.{engine}":
            return bool(check.get("ok"))
    return False


def _run_once(venv: Path, url: str, out: Path, max_steps: int, timeout: float) -> Run:
    proc, elapsed = _timed(
        [
            _bin(venv, "clickcast"),
            "auto",
            url,
            "--out",
            str(out),
            "--max-steps",
            str(max_steps),
        ],
        timeout=timeout,
    )
    run = Run(run_s=elapsed, exit_code=proc.returncode)
    sidecar = out.with_name(out.name + ".json")
    if proc.returncode != 0 or not sidecar.exists():
        print(proc.stderr[-3000:], file=sys.stderr)
        return run
    raw = sidecar.read_text(encoding="utf-8")
    data = json.loads(raw)
    run.sidecar_bytes = len(raw.encode("utf-8"))
    run.sidecar_chars = len(raw)
    run.steps = len(data.get("steps", []))
    run.schema_version = int(data.get("schema_version", 0))
    run.frames = int(data.get("media", {}).get("frames", 0) or 0)
    run.reel_bytes = out.stat().st_size if out.exists() else 0
    return run


def _validate_sidecar(venv: Path, sidecar: Path) -> tuple[bool, str]:
    """Load the sidecar through the shipped schema, in the venv under test."""
    code = (
        "import sys\n"
        "from clickcast.feedback import load\n"
        "report = load(sys.argv[1])\n"
        "print(report.schema_version, len(report.steps))\n"
    )
    proc = subprocess.run(
        [_bin(venv, "python"), "-c", code, str(sidecar)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[-500:]


def measure(args: argparse.Namespace) -> Measurement:
    """Run the whole measurement. Raises RuntimeError on a broken environment."""
    result = Measurement(pip_cold=not args.cache)
    workdir = (
        Path(args.workdir).expanduser()
        if args.workdir
        else Path(tempfile.mkdtemp(prefix="cc-bench-"))
    )
    workdir.mkdir(parents=True, exist_ok=True)
    venv = workdir / "venv"
    out = workdir / "tour.gif"

    if not args.reuse_venv:
        shutil.rmtree(venv, ignore_errors=True)
        started = time.perf_counter()
        subprocess.run([args.python, "-m", "venv", str(venv)], check=True)
        result.venv_s = time.perf_counter() - started

        pip_cmd = [_bin(venv, "pip"), "install", "--quiet", str(REPO_ROOT)]
        if not args.cache:
            pip_cmd.append("--no-cache-dir")
        proc, result.install_s = _timed(pip_cmd)
        if proc.returncode != 0:
            raise RuntimeError(f"pip install failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")

    report, result.doctor_s = _doctor(venv)
    result.doctor_ok = bool(report.get("ok"))
    if not _engine_ready(report, args.engine):
        if args.skip_engine_install:
            raise RuntimeError("browser engine missing and --skip-engine-install was passed")
        proc, result.engine_install_s = _timed(
            [_bin(venv, "clickcast"), "install", args.engine], timeout=1800
        )
        if proc.returncode != 0:
            raise RuntimeError(f"engine install failed:\n{proc.stderr[-2000:]}")
        report, result.doctor_s = _doctor(venv)
        result.doctor_ok = bool(report.get("ok"))
    if not result.doctor_ok:
        raise RuntimeError(f"doctor is not ok: {json.dumps(report, indent=2)[:2000]}")

    server, base_url = _serve(Path(args.fixture_dir))
    try:
        url = f"{base_url}/{args.page}"
        for index in range(args.repeat):
            if index:
                out.unlink(missing_ok=True)
                out.with_name(out.name + ".json").unlink(missing_ok=True)
            run = _run_once(venv, url, out, args.max_steps, args.timeout)
            if run.exit_code != 0:
                raise RuntimeError(f"auto failed with exit {run.exit_code}")
            valid, detail = _validate_sidecar(venv, out.with_name(out.name + ".json"))
            if not valid:
                raise RuntimeError(f"sidecar did not validate: {detail}")
            result.runs.append(run)
    finally:
        server.shutdown()
        server.server_close()

    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"artifacts kept in {workdir}", file=sys.stderr)
    return result


def summarize(result: Measurement) -> dict[str, Any]:
    runs = result.runs
    total = result.total_s
    return {
        "m1": {
            "venv_s": round(result.venv_s, 2),
            "install_s": round(result.install_s, 2),
            "doctor_s": round(result.doctor_s, 2),
            "engine_install_s": round(result.engine_install_s, 2),
            "first_run_s": round(runs[0].run_s, 2) if runs else 0.0,
            "total_s": round(total, 2),
            "pip_cold": result.pip_cold,
            "doctor_ok": result.doctor_ok,
        },
        "m2": {
            "runs": [
                {
                    "run_s": round(run.run_s, 2),
                    "steps": run.steps,
                    "schema_version": run.schema_version,
                    "sidecar_bytes": run.sidecar_bytes,
                    "bytes_per_step": round(run.bytes_per_step),
                    "estimated_tokens_per_step": round(run.estimated_tokens_per_step),
                    "reel_bytes": run.reel_bytes,
                    "frames": run.frames,
                }
                for run in runs
            ],
            "run_s_p50": round(percentile([r.run_s for r in runs], 50), 2),
            "run_s_p95": round(percentile([r.run_s for r in runs], 95), 2),
            "bytes_per_step_p50": round(percentile([r.bytes_per_step for r in runs], 50)),
            "estimated_tokens_per_step_p50": round(
                percentile([r.estimated_tokens_per_step for r in runs], 50)
            ),
            "estimator": "chars/4",
            "note": "observe/act/receipt ceilings arrive with the perception API",
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "repeat": len(runs),
        },
    }


def render_markdown(summary: dict[str, Any]) -> str:
    m1, m2 = summary["m1"], summary["m2"]
    lines = [
        "| Metric | Value |",
        "|---|---|",
        f"| venv | {m1['venv_s']} s |",
        f"| `pip install` ({'cold' if m1['pip_cold'] else 'warm cache'}) | {m1['install_s']} s |",
        f"| `doctor --json` | {m1['doctor_s']} s |",
        f"| browser install (tracked separately) | {m1['engine_install_s']} s |",
        f"| first `auto` tour | {m1['first_run_s']} s |",
        f"| **M1: install → verified report** | **{m1['total_s']} s** |",
        f"| M2: sidecar bytes/step (p50) | {m2['bytes_per_step_p50']} B |",
        f"| M2: estimated tokens/step (p50) | {m2['estimated_tokens_per_step_p50']} |",
        f"| tour wall (p50 / p95, n={summary['environment']['repeat']}) "
        f"| {m2['run_s_p50']} s / {m2['run_s_p95']} s |",
    ]
    return "\n".join(lines)


def check_ceilings(summary: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not summary["m1"]["pip_cold"] and summary["m1"]["total_s"] > WARM_TOTAL_CEILING_S:
        failures.append(
            f"M1 total {summary['m1']['total_s']}s exceeds {WARM_TOTAL_CEILING_S}s ceiling"
        )
    if summary["m2"]["bytes_per_step_p50"] > SIDECAR_BYTES_PER_STEP_CEILING:
        failures.append(
            f"sidecar {summary['m2']['bytes_per_step_p50']} B/step exceeds "
            f"{SIDECAR_BYTES_PER_STEP_CEILING} B ceiling"
        )
    return failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="tour runs on one install (default 1)"
    )
    parser.add_argument("--max-steps", type=int, default=2, help="auto steps per tour (default 2)")
    parser.add_argument("--engine", default="chromium", help="browser engine (default chromium)")
    parser.add_argument("--page", default="index.html", help="fixture page to tour")
    parser.add_argument("--fixture-dir", default=str(FIXTURE_DIR), help="static site to serve")
    parser.add_argument(
        "--python", default=sys.executable, help="interpreter used to create the venv"
    )
    parser.add_argument("--workdir", default=None, help="reuse a directory instead of a tempdir")
    parser.add_argument(
        "--reuse-venv", action="store_true", help="skip venv + pip (re-time tours only)"
    )
    parser.add_argument(
        "--cache",
        dest="cache",
        action="store_true",
        default=True,
        help="use the pip cache (default)",
    )
    parser.add_argument(
        "--no-cache-dir", dest="cache", action="store_false", help="cold pip install"
    )
    parser.add_argument(
        "--skip-engine-install", action="store_true", help="fail instead of downloading a browser"
    )
    parser.add_argument("--timeout", type=float, default=600.0, help="per-tour timeout in seconds")
    parser.add_argument("--keep", action="store_true", help="keep the venv/artifacts")
    parser.add_argument("--json-out", default=None, help="write the JSON summary here")
    parser.add_argument("--markdown-out", default=None, help="write the markdown table here")
    parser.add_argument(
        "--no-ceiling-check", action="store_true", help="report only; never fail on a ceiling"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not Path(args.fixture_dir).joinpath(args.page).exists():
        print(f"fixture page not found: {Path(args.fixture_dir) / args.page}", file=sys.stderr)
        return 2
    try:
        result = measure(args)
    except RuntimeError as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 1

    summary = summarize(result)
    markdown = render_markdown(summary)
    summary["markdown"] = markdown
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=2) + "\n")
    if args.markdown_out:
        Path(args.markdown_out).write_text(markdown + "\n")
    print(markdown)
    print()

    failures = [] if args.no_ceiling_check else check_ceilings(summary)
    for failure in failures:
        print(f"✗ {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
