from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from clickcast.feedback import (
    AccessibilityState,
    DiscoveredElement,
    ElementAccessibility,
    Media,
    PageState,
    Report,
    ReportBuilder,
    StepReport,
    load,
    write,
)

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "src" / "clickcast" / "feedback" / "schema" / "v4.json"
V3_SCHEMA_PATH = REPO_ROOT / "src" / "clickcast" / "feedback" / "schema" / "v3.json"
V2_SCHEMA_PATH = REPO_ROOT / "src" / "clickcast" / "feedback" / "schema" / "v2.json"


# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------


def _valid_media() -> Media:
    return Media(
        path="tour.gif",
        format="gif",
        size_bytes=1024,
        frame_count=12,
        duration_s=1.0,
        fps=12,
    )


def _valid_report() -> Report:
    return Report(
        clickcast_version="0.1.0",
        started_at="2026-07-23T15:00:00+00:00",
        duration_s=5.5,
        media=_valid_media(),
        steps=[
            StepReport(
                index=0,
                action="goto",
                args={"url": "https://x"},
                status="ok",
                duration_ms=1200.0,
                frames=["frame-0000-000.png"],
            )
        ],
    )


class TestModels:
    def test_media_positive_size(self) -> None:
        with pytest.raises(ValidationError):
            Media(path="x", format="gif", size_bytes=-1, frame_count=1, duration_s=0.1, fps=1)

    def test_page_state_caps_lists_at_50(self) -> None:
        with pytest.raises(ValidationError):
            PageState(console_errors=["e"] * 51)

    def test_discovered_element_bbox_needs_4(self) -> None:
        with pytest.raises(ValidationError):
            DiscoveredElement(selector="s", role="r", text="t", bbox=[0, 0, 1], score=1, source="x")

    def test_step_report_requires_status(self) -> None:
        with pytest.raises(ValidationError):
            StepReport(index=0, action="goto", duration_ms=1.0)  # type: ignore[call-arg]

    def test_report_default_schema_version_is_4(self) -> None:
        # Bumped to 4 in #196/#199: the optional `elements[].accessibility`
        # block ships on `DiscoveredElement`. v1/v2/v3 sidecars still
        # validate under this model (see the forward-compat test below and
        # the v1/v2/v3-backcompat tests further down).
        assert _valid_report().schema_version == 4

    def test_report_defaults_are_forward_compatible(self) -> None:
        # Roadmap #29 Track C adds a top-level `graph` block. The base model
        # must accept unknown top-level keys silently so a v2 file can round-
        # trip through a v1 parser without exploding on strict-extras.
        payload = _valid_report().model_dump()
        payload["graph"] = {"nodes": [], "edges": []}
        # Should NOT raise
        Report.model_validate(payload)

    def test_v2_sidecar_validates_under_v3_model(self) -> None:
        # See #151 (AI-2, AI-5): v2 sidecars (no `skip_reason`, no
        # `error_code`, no `schema_version` bump) must round-trip cleanly
        # through the v3 model — every added field is optional and defaults
        # to `None`. Otherwise downstream consumers that hoard v2 baselines
        # break on the model change.
        v2_payload = {
            "schema_version": 2,
            "clickcast_version": "0.2.4",
            "started_at": "2026-07-30T15:00:00+00:00",
            "duration_s": 5.5,
            "media": {
                "path": "tour.gif",
                "format": "gif",
                "size_bytes": 1024,
                "frame_count": 12,
                "duration_s": 1.0,
                "fps": 12,
            },
            "steps": [
                {
                    "index": 0,
                    "action": "goto",
                    "args": {"url": "https://x"},
                    "status": "ok",
                    "duration_ms": 1200.0,
                    "frames": ["frame-0000-000.png"],
                }
            ],
        }
        # Should NOT raise — new gate fields default to None.
        report = Report.model_validate(v2_payload)
        assert report.schema_version == 2  # writer's value preserved
        assert report.steps[0].skip_reason is None
        assert report.steps[0].error_code is None

    def test_v3_report_default_step_has_none_gates(self) -> None:
        # A well-formed v3 report built from the model defaults carries
        # `None` for both new gate fields — the shipped absence-signal.
        report = _valid_report()
        assert report.steps[0].skip_reason is None
        assert report.steps[0].error_code is None

    def test_v3_sidecar_validates_under_v4_model(self) -> None:
        # See #196/#199: a v3 sidecar (no `discovered_elements[].accessibility`,
        # no `schema_version` bump) must round-trip cleanly through the v4
        # model — the new field is optional and defaults to `None`.
        v3_payload = {
            "schema_version": 3,
            "clickcast_version": "0.2.9",
            "started_at": "2026-08-07T15:00:00+00:00",
            "duration_s": 5.5,
            "media": {
                "path": "tour.gif",
                "format": "gif",
                "size_bytes": 1024,
                "frame_count": 12,
                "duration_s": 1.0,
                "fps": 12,
            },
            "discovered_elements": [
                {
                    "selector": 'role=button[name="Get started"]',
                    "role": "button",
                    "text": "Get started",
                    "bbox": [10, 10, 100, 30],
                    "score": 3,
                    "source": "dom-heuristic",
                }
            ],
            "steps": [
                {
                    "index": 0,
                    "action": "goto",
                    "args": {"url": "https://x"},
                    "status": "ok",
                    "duration_ms": 1200.0,
                    "frames": ["frame-0000-000.png"],
                }
            ],
        }
        # Should NOT raise — the new accessibility field defaults to None.
        report = Report.model_validate(v3_payload)
        assert report.schema_version == 3  # writer's value preserved
        assert report.discovered_elements[0].accessibility is None

    def test_discovered_element_accessibility_defaults_none(self) -> None:
        el = DiscoveredElement(
            selector="s", role="button", text="t", bbox=[0, 0, 1, 1], score=1, source="x"
        )
        assert el.accessibility is None

    def test_discovered_element_accessibility_populated(self) -> None:
        el = DiscoveredElement(
            selector='role=button[name="Get started"]',
            role="button",
            text="Get started",
            bbox=[10, 10, 100, 30],
            score=3,
            source="dom-heuristic",
            accessibility=ElementAccessibility(
                role="button",
                name="Get started",
                state=AccessibilityState(disabled=False),
                grid_cell=[1, 0],
            ),
        )
        assert el.accessibility is not None
        assert el.accessibility.role == "button"
        assert el.accessibility.name == "Get started"
        assert el.accessibility.state.disabled is False
        assert el.accessibility.grid_cell == [1, 0]

    def test_accessibility_state_checked_accepts_mixed_string(self) -> None:
        # Tri-state checkboxes report `checked="mixed"` — not a plain bool.
        state = AccessibilityState(checked="mixed")
        assert state.checked == "mixed"

    def test_element_accessibility_grid_cell_needs_2(self) -> None:
        with pytest.raises(ValidationError):
            ElementAccessibility(grid_cell=[1])

    def test_element_accessibility_forbids_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            ElementAccessibility(role="button", bogus="nope")  # type: ignore[call-arg]


# ------------------------------------------------------------------
# JSON Schema — model_json_schema() must match the committed file
# ------------------------------------------------------------------


class TestJsonSchema:
    def test_committed_schema_matches_model(self) -> None:
        emitted = Report.model_json_schema()
        assert SCHEMA_PATH.exists(), (
            "committed schema missing — run `python scripts/gen_feedback_schema.py`"
        )
        committed = json.loads(SCHEMA_PATH.read_text())
        assert emitted == committed, (
            "committed schema is stale — run `python scripts/gen_feedback_schema.py` "
            "and commit the update"
        )

    def test_schema_advertises_v4(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text())
        # schema_version has default 4 in the model — check the default made it
        assert schema["properties"]["schema_version"]["default"] == 4

    def test_schema_carries_element_accessibility_block(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text())
        defs = schema["$defs"]
        assert "ElementAccessibility" in defs
        assert "AccessibilityState" in defs
        discovered_props = defs["DiscoveredElement"]["properties"]
        assert "accessibility" in discovered_props

    def test_v1_v2_v3_schemas_preserved(self) -> None:
        # All three older snapshots stay on disk verbatim — downstream
        # consumers that bookmarked those URLs must keep working. See
        # #151 (v1→v2, v2→v3) and #196 (v3→v4).
        v1_path = REPO_ROOT / "src" / "clickcast" / "feedback" / "schema" / "v1.json"
        assert v1_path.exists(), "v1.json must never be deleted (historical contract)"
        assert V2_SCHEMA_PATH.exists(), "v2.json must never be deleted (historical contract)"
        assert V3_SCHEMA_PATH.exists(), "v3.json must never be deleted (historical contract)"
        v3_schema = json.loads(V3_SCHEMA_PATH.read_text())
        # v3.json must NOT carry the v4 accessibility block — it's a frozen
        # snapshot of the schema BEFORE #196 landed.
        assert "ElementAccessibility" not in v3_schema.get("$defs", {})


# ------------------------------------------------------------------
# Round-trip + load()/write()
# ------------------------------------------------------------------


class TestRoundTrip:
    def test_write_then_load_returns_equal_report(self, tmp_path: Path) -> None:
        original = _valid_report()
        path = write(original, tmp_path / "tour.gif.json")
        loaded = load(path)
        assert loaded == original

    def test_serialization_is_json(self, tmp_path: Path) -> None:
        path = write(_valid_report(), tmp_path / "tour.gif.json")
        # Must be valid JSON with predictable indentation
        payload = json.loads(path.read_text())
        assert payload["schema_version"] == 4
        assert payload["media"]["format"] == "gif"

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "does-not-exist.json")


# ------------------------------------------------------------------
# Builder unit — no browser
# ------------------------------------------------------------------


class TestBuilder:
    def test_finalises_without_attach(self) -> None:
        # A builder can be built even if no session was ever attached — used
        # for tests + dry runs where we still want media metadata.
        builder = ReportBuilder(url="https://x", engine="chromium", viewport=(400, 300))
        report = builder.build(_valid_media())
        assert report.url == "https://x"
        assert report.viewport == [400, 300]
        assert report.discovered_elements == []
        assert report.steps == []

    def test_add_warning_and_error_propagate(self) -> None:
        builder = ReportBuilder(engine="chromium")
        builder.add_warning("hydration was slow")
        builder.add_error("goto returned 500")
        report = builder.build(_valid_media())
        assert report.warnings == ["hydration was slow"]
        assert report.errors == ["goto returned 500"]


# ------------------------------------------------------------------
# Consumer example — the tests/consumer/read_sidecar.py script
# ------------------------------------------------------------------


class TestConsumerExample:
    def test_consumer_lists_failed_step_frames(self, tmp_path: Path) -> None:
        # Build a report with one ok + one failed step, write it, then
        # invoke the consumer script as a subprocess — proves the sidecar is
        # usable from outside the package without importing it.
        report = Report(
            clickcast_version="0.1.0",
            started_at="2026-07-23T15:00:00+00:00",
            duration_s=1.0,
            media=_valid_media(),
            steps=[
                StepReport(
                    index=0,
                    action="goto",
                    args={"url": "https://x"},
                    status="ok",
                    duration_ms=100.0,
                    frames=["frame-0000-000.png"],
                ),
                StepReport(
                    index=1,
                    action="click",
                    args={"selector": "#gone"},
                    status="failed",
                    duration_ms=250.0,
                    frames=["frame-0001-000.png", "frame-0001-001.png"],
                    error="TimeoutError: locator not found",
                ),
            ],
        )
        sidecar = write(report, tmp_path / "tour.gif.json")
        script = REPO_ROOT / "tests" / "consumer" / "read_sidecar.py"
        result = subprocess.run(
            [sys.executable, str(script), str(sidecar)],
            capture_output=True,
            text=True,
            check=True,
        )
        # Consumer prints: "<index> <action> -> <frames_csv>" per failed step
        assert "1 click" in result.stdout
        assert "frame-0001-000.png,frame-0001-001.png" in result.stdout

    def test_consumer_reads_accessibility_block(self, tmp_path: Path) -> None:
        # #196/#200: a standalone (no `clickcast` import) consumer must be
        # able to read the v4 `discovered_elements[].accessibility` block.
        # Mirrors `read_sidecar.py`'s pattern — build+write a report here
        # (no browser needed for this shape-level contract test), then
        # invoke the consumer script as a real subprocess.
        report = Report(
            clickcast_version="0.2.9",
            started_at="2026-09-03T15:00:00+00:00",
            duration_s=1.0,
            media=_valid_media(),
            discovered_elements=[
                DiscoveredElement(
                    selector='role=button[name="Get started"]',
                    role="button",
                    text="Get started",
                    bbox=[10, 20, 100, 30],
                    score=3,
                    source="dom-heuristic",
                    accessibility=ElementAccessibility(
                        role="button",
                        name="Get started",
                        state=AccessibilityState(disabled=False),
                        grid_cell=[0, 0],
                    ),
                ),
                DiscoveredElement(
                    selector="#unlabeled",
                    role="button",
                    text="",
                    bbox=[10, 60, 40, 20],
                    score=1,
                    source="dom-heuristic",
                    # No accessibility captured for this one — null, additive.
                    accessibility=None,
                ),
            ],
        )
        sidecar = write(report, tmp_path / "tour.gif.json")
        script = REPO_ROOT / "tests" / "consumer" / "read_accessibility.py"
        result = subprocess.run(
            [sys.executable, str(script), str(sidecar)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert (
            'role=button[name="Get started"] role=button name=Get started '
            "disabled=False grid_cell=0,0" in result.stdout
        )
        # The null-accessibility element produced no output line.
        assert "#unlabeled" not in result.stdout
