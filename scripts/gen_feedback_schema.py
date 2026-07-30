"""Regenerate the committed feedback JSON Schema from the pydantic Report.

Run:

    python scripts/gen_feedback_schema.py

Emits ``src/clickcast/feedback/schema/v2.json`` from the current
:class:`~clickcast.feedback.models.Report`. The test suite compares the
emitted schema to the committed one — a mismatch means the model
changed. Bump ``schema_version`` and update the file.

The legacy ``v1.json`` file is preserved verbatim as an immutable
snapshot of the schema before the #107 graph block landed — downstream
consumers that bookmarked that URL keep working. New consumers should
target ``v2.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from clickcast.feedback.models import Report

SCHEMA_DIR = Path(__file__).parent.parent / "src" / "clickcast" / "feedback" / "schema"
SCHEMA_PATH = SCHEMA_DIR / "v2.json"


def main() -> None:
    schema = Report.model_json_schema()
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {SCHEMA_PATH} ({SCHEMA_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
