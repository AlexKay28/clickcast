"""Minimal AI-facing consumer of a clickcast sidecar.

Usage:

    python tests/consumer/read_sidecar.py <path/to/tour.gif.json>

Prints one line per failed step:

    <step_index> <action> -> <frame1,frame2,...>

This script deliberately does NOT import the `clickcast` package — a
downstream consumer only needs the JSON + a schema. If this stops working,
it means the sidecar shape has drifted from what agents can rely on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: read_sidecar.py <path>", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text())
    if "steps" not in payload:
        print("sidecar missing `steps` block", file=sys.stderr)
        return 1
    for step in payload["steps"]:
        if step.get("status") == "failed":
            frames = ",".join(step.get("frames", []))
            print(f"{step['index']} {step['action']} -> {frames}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
