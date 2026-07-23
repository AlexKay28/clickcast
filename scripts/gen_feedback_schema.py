"""Regenerate the committed feedback JSON Schema from the pydantic Report.

Run:

    python scripts/gen_feedback_schema.py

The test suite compares the emitted schema to the committed one — a mismatch
means the model changed. Bump ``schema_version`` and update the file.
"""

from __future__ import annotations

import json
from pathlib import Path

from clickcast.feedback.models import Report

SCHEMA_PATH = Path(__file__).parent.parent / "src" / "clickcast" / "feedback" / "schema" / "v1.json"


def main() -> None:
    schema = Report.model_json_schema()
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"wrote {SCHEMA_PATH} ({SCHEMA_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
