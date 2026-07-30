"""Tests for :mod:`clickcast.skill` and the ``clickcast skill`` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from clickcast import __version__ as CLICKCAST_VERSION
from clickcast.cli import app
from clickcast.feedback.pointers import (
    DIAGNOSTICS_COMMAND,
    DOCS_URL,
    REPORT_URL,
)
from clickcast.feedback.pointers import SCHEMA_URL as AGENT_REPORT_SCHEMA_URL
from clickcast.skill import (
    COMMAND_BRIEFS,
    SIDECAR_SCHEMA_URL,
    SKILL_SCHEMA_VERSION,
    build_payload,
    render_markdown,
)

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "skill-schema" / "v1.json"


def _registered_name(cmd: object) -> str:
    """Return the CLI-visible name for a Typer registered command.

    Typer stores the explicit ``name=`` on ``cmd.name`` when set and falls
    back to the callback function's ``__name__`` (with underscores swapped
    for hyphens, matching Typer's own naming rule) otherwise.
    """
    explicit = getattr(cmd, "name", None)
    if explicit:
        return str(explicit)
    callback = getattr(cmd, "callback", None)
    fn_name = getattr(callback, "__name__", "")
    return fn_name.replace("_", "-")


def _registered_command_names() -> set[str]:
    """All CLI-visible top-level names on the root app.

    Since #124, ``clickcast feedback …`` is a sub-app registered via
    ``app.add_typer`` — it lands on ``registered_groups``, not
    ``registered_commands``. Both surfaces need a skill brief so an
    agent that sees ``clickcast feedback`` in ``--help`` can find its
    entry in ``clickcast skill`` too.
    """
    names = {_registered_name(cmd) for cmd in app.registered_commands}
    names |= {_registered_name(grp) for grp in app.registered_groups}
    return names


def _assert_matches_schema(payload: dict) -> None:
    """Hand-rolled structural check against docs/skill-schema/v1.json.

    Full JSON Schema validation would require adding `jsonschema` as a test
    dependency; the shape is small enough that a targeted check catches the
    drift cases that matter (missing required keys, wrong types) without the
    extra install.
    """
    schema = json.loads(_SCHEMA_PATH.read_text())
    for key in schema["required"]:
        assert key in payload, f"missing top-level key {key!r}"
    assert payload["schema_version"] == schema["properties"]["schema_version"]["const"]
    for key in schema["properties"]["contracts"]["required"]:
        assert key in payload["contracts"], f"contracts missing {key!r}"
    for key in schema["properties"]["feedback"]["required"]:
        assert key in payload["feedback"], f"feedback missing {key!r}"
    assert isinstance(payload["commands"], list) and payload["commands"]
    cmd_keys = schema["$defs"]["CommandBrief"]["required"]
    flag_keys = schema["$defs"]["FlagBrief"]["required"]
    for cmd in payload["commands"]:
        for key in cmd_keys:
            assert key in cmd, f"command {cmd.get('name')!r} missing {key!r}"
        for flag in cmd["key_flags"]:
            for key in flag_keys:
                assert key in flag, f"flag on {cmd['name']!r} missing {key!r}"


class TestDriftGuard:
    """The skill brief must stay in lockstep with the registered subcommands."""

    def test_every_registered_command_has_a_brief(self) -> None:
        registered = _registered_command_names()
        briefed = {c.name for c in COMMAND_BRIEFS}
        missing = registered - briefed
        assert not missing, (
            f"registered commands without a skill brief: {sorted(missing)}. "
            "Add an entry to COMMAND_BRIEFS in src/clickcast/skill.py."
        )

    def test_no_dangling_brief_entries(self) -> None:
        registered = _registered_command_names()
        briefed = {c.name for c in COMMAND_BRIEFS}
        dangling = briefed - registered
        assert not dangling, (
            f"skill briefs reference commands that no longer exist: {sorted(dangling)}."
        )


class TestPayload:
    def test_matches_committed_schema(self) -> None:
        schema = json.loads(_SCHEMA_PATH.read_text())
        _assert_matches_schema(build_payload())
        assert schema  # keep the read live so a corrupt schema fails here too

    def test_schema_version_and_clickcast_version_present(self) -> None:
        payload = build_payload()
        assert payload["schema_version"] == SKILL_SCHEMA_VERSION
        assert payload["clickcast_version"] == CLICKCAST_VERSION

    def test_all_four_feedback_pointers_present(self) -> None:
        payload = build_payload()
        assert payload["feedback"]["report_url"] == REPORT_URL
        assert payload["feedback"]["schema_url"] == AGENT_REPORT_SCHEMA_URL
        assert payload["feedback"]["docs_url"] == DOCS_URL
        assert payload["feedback"]["diagnostics_command"] == DIAGNOSTICS_COMMAND

    def test_contracts_reference_both_schemas(self) -> None:
        payload = build_payload()
        assert payload["contracts"]["sidecar_schema_url"] == SIDECAR_SCHEMA_URL
        assert payload["contracts"]["agent_report_schema_url"] == AGENT_REPORT_SCHEMA_URL

    def test_skill_command_documents_json_flag(self) -> None:
        payload = build_payload()
        skill_entry = next(c for c in payload["commands"] if c["name"] == "skill")
        flags = [f["flag"] for f in skill_entry["key_flags"]]
        assert any("--json" in f for f in flags)


class TestMarkdown:
    def test_contains_every_command(self) -> None:
        md = render_markdown()
        for c in COMMAND_BRIEFS:
            assert f"clickcast {c.name}" in md

    def test_contains_all_four_feedback_pointers(self) -> None:
        md = render_markdown()
        assert REPORT_URL in md
        assert AGENT_REPORT_SCHEMA_URL in md
        assert DOCS_URL in md
        assert DIAGNOSTICS_COMMAND in md

    def test_contains_sidecar_schema_url(self) -> None:
        assert SIDECAR_SCHEMA_URL in render_markdown()

    def test_word_count_under_900(self) -> None:
        # The proposal target was ~500 words; the cap gives room for growth
        # without letting the brief bloat into a manual. Bumped from 800 to
        # 900 when `feedback` (#124) added the 12th command — legitimate
        # growth from a new command shouldn't force pruning the other 11.
        words = len(render_markdown().split())
        assert words < 900, f"brief has grown to {words} words — trim it"

    def test_starts_with_versioned_header(self) -> None:
        assert render_markdown().splitlines()[0] == (
            f"# clickcast — AI agent brief (v{CLICKCAST_VERSION})"
        )


class TestCLI:
    runner = CliRunner()

    def test_default_prints_markdown(self) -> None:
        result = self.runner.invoke(app, ["skill"])
        assert result.exit_code == 0
        assert result.stdout.startswith("# clickcast")
        assert "clickcast auto" in result.stdout

    def test_json_flag_emits_valid_payload(self) -> None:
        result = self.runner.invoke(app, ["skill", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        _assert_matches_schema(payload)

    def test_json_output_is_deterministic(self) -> None:
        a = self.runner.invoke(app, ["skill", "--json"]).stdout
        b = self.runner.invoke(app, ["skill", "--json"]).stdout
        assert a == b, "skill --json should be byte-deterministic given a version"

    def test_help_carries_feedback_epilog(self) -> None:
        result = self.runner.invoke(app, ["skill", "--help"])
        assert REPORT_URL in result.stdout
