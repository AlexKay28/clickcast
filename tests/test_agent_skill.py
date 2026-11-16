"""Validate the root Agent Skills file.

`SKILL.md` is the manifest popular agent harnesses load when this repo is
installed as a skill. Keep its frontmatter canonical, the description
discoverable, and the cross-referenced docs real so downstream agents don't
get a broken entrypoint.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from clickcast.cli import app

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"


def _frontmatter() -> dict[str, str]:
    text = SKILL_PATH.read_text()
    assert text.startswith("---\n")
    block = text[4 : text.index("\n---\n", 4)]
    return yaml.safe_load(block)


class TestAgentSkillManifest:
    def test_frontmatter_name_matches_repo_and_is_valid(self) -> None:
        meta = _frontmatter()
        assert set(meta) == {"name", "description"}
        assert meta["name"] == "clickcast"
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", meta["name"])
        assert "\n" not in meta["description"]

    def test_description_mentions_core_capabilities(self) -> None:
        description = _frontmatter()["description"].lower()
        for term in ("playwright", "gif", "mp4", "sidecar", "mcp", "tour", "scenario"):
            assert term in description, f"missing {term!r} in skill description"

    def test_body_covers_every_documented_mode(self) -> None:
        text = SKILL_PATH.read_text()
        for mode in ("auto", "run", "shot", "explore", "mcp", "smooth", "annotations"):
            assert mode in text, f"SKILL.md should document {mode}"

    def test_referenced_cli_commands_exist(self) -> None:
        text = SKILL_PATH.read_text()
        commands: set[str] = set()
        for block in re.findall(r"```(?:bash|sh|shell)\n(.*?)```", text, re.DOTALL):
            commands.update(re.findall(r"^\s*clickcast ([a-z-]+)\b", block, re.MULTILINE))
        assert commands
        registered = {
            cmd.name or (cmd.callback.__name__.replace("_", "-").removesuffix("-cmd"))
            for cmd in app.registered_commands
        }
        unknown = commands - registered - {"install"}
        assert not unknown, f"SKILL.md mentions unknown commands: {sorted(unknown)}"

    def test_local_document_references_resolve(self) -> None:
        text = SKILL_PATH.read_text()
        refs = {
            ref
            for ref in re.findall(r"`([^`\n]+\.(?:md|json|toml))`", text)
            if "/" in ref
            or ref in {"README.md", "SKILL.md", "skill.md", "server.json", "pyproject.toml"}
        }
        for ref in refs:
            if "/" in ref:
                candidate = ROOT / ref
            else:
                candidate = ROOT / ref
                if not candidate.exists():
                    candidate = ROOT / "docs" / ref
            assert candidate.exists(), f"SKILL.md references missing file: {ref}"

    def test_reference_guide_is_linked_and_present(self) -> None:
        text = SKILL_PATH.read_text()
        reference = ROOT / "references" / "REFERENCE.md"
        assert "references/REFERENCE.md" in text
        assert reference.is_file()
        assert reference.stat().st_size > 0

    def test_size_stays_reasonable_for_harness_contexts(self) -> None:
        lines = SKILL_PATH.read_text().splitlines()
        assert len(lines) <= 500, "SKILL.md should stay under the recommended 500-line limit"
