"""``clickcast skill`` — single-message self-introduction for AI agents.

Design decisions (see #103):

- **Hand-authored narrative, introspected command list.** Command names come
  from the live Typer app so a newly added subcommand can't silently miss
  the brief (the drift-guard test in ``tests/test_skill.py`` fails). The
  narrative fields (``when_to_use``, ``key_flags``, ``example``) stay
  hand-authored because they're the point of the whole feature — a
  generated brief would just recite ``--help``.
- **Two renderers, one source of truth.** ``render_markdown`` and
  ``build_payload`` both consume :data:`COMMAND_BRIEFS`; drift between the
  two output formats is not possible.
- **No external fetches.** The output is fully self-contained. Schema
  URLs are referenced by absolute URL so agents *can* fetch schema-level
  detail if they want to, but the brief itself is the answer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from clickcast import __version__ as CLICKCAST_VERSION
from clickcast.feedback.pointers import (
    DIAGNOSTICS_COMMAND,
    DOCS_URL,
    REPORT_URL,
)
from clickcast.feedback.pointers import (
    SCHEMA_URL as AGENT_REPORT_SCHEMA_URL,
)

__all__ = [
    "COMMAND_BRIEFS",
    "SIDECAR_SCHEMA_URL",
    "SKILL_SCHEMA_VERSION",
    "SUMMARY",
    "CommandBrief",
    "FlagBrief",
    "build_payload",
    "render_markdown",
]

SKILL_SCHEMA_VERSION = 1

SIDECAR_SCHEMA_URL = (
    "https://raw.githubusercontent.com/AlexKay28/clickcast/main/"
    "src/clickcast/feedback/schema/v1.json"
)

SUMMARY = (
    "You are driving a browser through a website and returning a reel "
    "(GIF/MP4) plus an AI-readable JSON sidecar. Use clickcast when you "
    "need to (a) show a human what happened during a UI walk-through, or "
    "(b) get a machine-parseable summary of a page's interactive elements "
    "and what happened when you clicked them."
)


@dataclass(frozen=True, slots=True)
class FlagBrief:
    flag: str
    why: str


@dataclass(frozen=True, slots=True)
class CommandBrief:
    name: str
    summary: str
    when_to_use: str
    key_flags: tuple[FlagBrief, ...] = field(default_factory=tuple)
    example: str = ""


COMMAND_BRIEFS: tuple[CommandBrief, ...] = (
    CommandBrief(
        name="auto",
        summary="One-shot BFS/DFS tour of a URL — no scenario file needed.",
        when_to_use=(
            "You have a URL and want clickcast to discover and click through "
            "interactive elements automatically."
        ),
        key_flags=(
            FlagBrief("--max-steps N", "click budget across the whole tour (default 15)"),
            FlagBrief("--max-pages N", "cap on how many pages the tour visits (default 5)"),
            FlagBrief(
                "--pace natural|fast|slow|onboarding",
                "speed preset — sets --fps and --dwell together",
            ),
            FlagBrief(
                "--with-feedback",
                "attach the AI-agent feedback pointer block to the sidecar",
            ),
            FlagBrief(
                "--zoom-on-click N",
                "crop-and-scale post-click frames around the click point",
            ),
            FlagBrief("--seed-url URL", "extra URLs to visit in a fixed order"),
        ),
        example="clickcast auto https://example.com --with-feedback --pace fast",
    ),
    CommandBrief(
        name="run",
        summary="Deterministic scripted tour from a YAML scenario.",
        when_to_use=(
            "You have (or generate) a precise step list and want repeatable "
            "output. Preferred over `auto` when you know exactly which "
            "selectors to click."
        ),
        key_flags=(
            FlagBrief("--out PATH", "override the scenario's `meta.out`"),
            FlagBrief("--var key=value", "inject a scenario variable"),
            FlagBrief("--with-feedback", "attach the AI-agent feedback pointer block"),
        ),
        example="clickcast run tour.yml --with-feedback",
    ),
    CommandBrief(
        name="shot",
        summary="Capture a single annotated screenshot.",
        when_to_use=(
            "You want one frame, not a reel — e.g. proving a page renders "
            "correctly after a login step."
        ),
        key_flags=(FlagBrief("--out PATH", "output image path"),),
        example="clickcast shot https://example.com --out landing.png",
    ),
    CommandBrief(
        name="init",
        summary="Scaffold a starter scenario file from a live page.",
        when_to_use=(
            "You want to start from a real page's discovered elements rather "
            "than write YAML by hand."
        ),
        key_flags=(),
        example="clickcast init https://example.com --out tour.yml",
    ),
    CommandBrief(
        name="elements",
        summary="Dump the interactive elements clickcast can see on a page.",
        when_to_use=(
            "You want to reason about a page's selectors without recording "
            "anything — e.g. deciding what to script in a `run` scenario."
        ),
        key_flags=(FlagBrief("--limit N", "cap on how many elements to return"),),
        example="clickcast elements https://example.com --limit 20",
    ),
    CommandBrief(
        name="assertions",
        summary="Distill a sidecar to its CI-stable assertion set (optionally diff a baseline).",
        when_to_use=(
            "You want a two-line CI regression gate: distill a fresh sidecar "
            "and compare it to a committed baseline. Byte-identical across "
            "runs — timestamps, frame paths, and URL query strings are "
            "excluded. See docs/assertions-schema/v1.json."
        ),
        key_flags=(
            FlagBrief(
                "--baseline PATH", "diff current vs a committed baseline (nonzero exit on drift)"
            ),
            FlagBrief("--json", "emit the distilled JSON (or drift payload) on stdout"),
        ),
        example="clickcast assertions reel.gif.json --baseline golden.json",
    ),
    CommandBrief(
        name="report-bug",
        summary="Turn a sidecar into an actionable AI-agent bug report.",
        when_to_use=(
            "Something in a reel or sidecar looks wrong. Produces diagnostics "
            "plus a prefilled GitHub issue URL. See docs/for-agents.md."
        ),
        key_flags=(
            FlagBrief("--json", "emit the Track-C payload verbatim (see schema below)"),
            FlagBrief("--open", "launch the prefilled issue URL in a browser"),
            FlagBrief(
                "--redact/--no-redact",
                "sanitize URLs, selectors, visible text (default on)",
            ),
            FlagBrief("--note TEXT", "free-text environment note"),
        ),
        example="clickcast report-bug reel.gif.json --json",
    ),
    CommandBrief(
        name="doctor",
        summary="Diagnose the local environment (Python, playwright, engines, ffmpeg).",
        when_to_use=(
            "Something failed early and you want to rule out setup issues before filing a bug."
        ),
        key_flags=(FlagBrief("--json", "machine-readable output"),),
        example="clickcast doctor --json",
    ),
    CommandBrief(
        name="config",
        summary="Read / write persistent defaults for other subcommands.",
        when_to_use=(
            "You want the same default engine / viewport / pace across many "
            "runs without repeating flags."
        ),
        key_flags=(),
        example="clickcast config set auto.pace fast",
    ),
    CommandBrief(
        name="install",
        summary="Install browser engines (wraps `playwright install`).",
        when_to_use="First-run setup or after upgrading playwright.",
        key_flags=(
            FlagBrief("--with-deps", "also install system libraries (needs sudo on Linux)"),
        ),
        example="clickcast install chromium --with-deps",
    ),
    CommandBrief(
        name="skill",
        summary="This message — an AI-friendly brief of everything clickcast can do.",
        when_to_use=(
            "You are an AI agent that just met clickcast and want the whole "
            "capability surface in one message."
        ),
        key_flags=(
            FlagBrief("--json", "emit as a JSON object matching docs/skill-schema/v1.json"),
        ),
        example="clickcast skill --json",
    ),
)


def build_payload() -> dict[str, Any]:
    """JSON-shaped payload matching ``docs/skill-schema/v1.json``."""
    return {
        "schema_version": SKILL_SCHEMA_VERSION,
        "clickcast_version": CLICKCAST_VERSION,
        "summary": SUMMARY,
        "commands": [_command_dict(c) for c in COMMAND_BRIEFS],
        "contracts": {
            "sidecar_schema_url": SIDECAR_SCHEMA_URL,
            "agent_report_schema_url": AGENT_REPORT_SCHEMA_URL,
            "agent_docs_url": DOCS_URL,
        },
        "feedback": {
            "report_url": REPORT_URL,
            "schema_url": AGENT_REPORT_SCHEMA_URL,
            "docs_url": DOCS_URL,
            "diagnostics_command": DIAGNOSTICS_COMMAND,
        },
    }


def render_markdown() -> str:
    """Markdown brief — the default output of ``clickcast skill``."""
    lines: list[str] = []
    lines.append(f"# clickcast — AI agent brief (v{CLICKCAST_VERSION})")
    lines.append("")
    lines.append(SUMMARY)
    lines.append("")
    lines.append("## Commands")
    lines.append("")
    for cmd in COMMAND_BRIEFS:
        lines.append(f"### `clickcast {cmd.name}` — {cmd.summary}")
        lines.append(f"When: {cmd.when_to_use}")
        if cmd.key_flags:
            lines.append("Key flags:")
            for f in cmd.key_flags:
                lines.append(f"  - `{f.flag}` — {f.why}")
        if cmd.example:
            lines.append(f"Example: `{cmd.example}`")
        lines.append("")
    lines.append("## Machine contracts")
    lines.append("")
    lines.append(f"- Reel sidecar: {SIDECAR_SCHEMA_URL}")
    lines.append(f"- Agent bug-report payload: {AGENT_REPORT_SCHEMA_URL}")
    lines.append(f"- Full agent docs: {DOCS_URL}")
    lines.append("")
    lines.append("## Feedback loop")
    lines.append("")
    lines.append(
        f"If something breaks, run `{DIAGNOSTICS_COMMAND}` to get diagnostics "
        f"plus a prefilled GitHub issue URL. Or file at:"
    )
    lines.append(REPORT_URL)
    return "\n".join(lines)


def _command_dict(cmd: CommandBrief) -> dict[str, Any]:
    d = asdict(cmd)
    d["key_flags"] = [asdict(f) for f in cmd.key_flags]
    return d
