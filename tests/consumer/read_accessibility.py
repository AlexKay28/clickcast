"""Minimal AI-facing consumer of a clickcast v4 sidecar's accessibility block.

Usage:

    python tests/consumer/read_accessibility.py <path/to/tour.gif.json>

Prints one line per discovered element that carries a v4
``accessibility`` block:

    <selector> role=<role> name=<name> disabled=<disabled> grid_cell=<col,row>

Deliberately does NOT import the `clickcast` package — a downstream
consumer only needs the JSON + the published ``schema/v4.json``. Mirrors
``tests/consumer/read_sidecar.py`` (see #99); this script exercises the
#196 accessibility+grid fusion block specifically. If this stops working,
it means the ``elements[].accessibility`` shape has drifted from what
agents can rely on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: read_accessibility.py <path>", file=sys.stderr)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text())
    if "discovered_elements" not in payload:
        print("sidecar missing `discovered_elements` block", file=sys.stderr)
        return 1
    for element in payload["discovered_elements"]:
        a11y = element.get("accessibility")
        if a11y is None:
            continue
        state = a11y.get("state") or {}
        grid_cell = a11y.get("grid_cell")
        cell_str = ",".join(str(c) for c in grid_cell) if grid_cell is not None else "-"
        print(
            f"{element['selector']} role={a11y.get('role')} name={a11y.get('name')} "
            f"disabled={state.get('disabled')} grid_cell={cell_str}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
