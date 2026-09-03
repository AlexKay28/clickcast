"""Regenerate the committed feedback JSON Schema from the pydantic Report.

Run:

    python scripts/gen_feedback_schema.py

Emits ``src/clickcast/feedback/schema/v4.json`` from the current
:class:`~clickcast.feedback.models.Report`. The test suite compares the
emitted schema to the committed one — a mismatch means the model
changed. Bump ``schema_version`` and update the file.

The legacy ``v1.json``, ``v2.json``, and ``v3.json`` files are preserved
verbatim as immutable snapshots of the schema before the #107 graph block
landed (v1), before the #151 ``skip_reason`` / ``error_code`` gates landed
(v2), and before the #196 ``elements[].accessibility`` block landed (v3).
Downstream consumers that bookmarked those URLs keep working. New
consumers should target ``v4.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from clickcast.feedback.models import Report

SCHEMA_DIR = Path(__file__).parent.parent / "src" / "clickcast" / "feedback" / "schema"
SCHEMA_PATH = SCHEMA_DIR / "v4.json"


def main() -> None:
    schema = Report.model_json_schema()
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {SCHEMA_PATH} ({SCHEMA_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
