"""``clickcast report-bug`` — turn a sidecar into an actionable bug report.

Produces both human-readable diagnostics (stdout) and a prefilled GitHub
issue URL. Optionally emits the report as JSON matching the Track-C schema at
``docs/agent-report-schema/v1.json`` so downstream agents can POST it or
persist it verbatim.
"""

from __future__ import annotations

import json
import platform
import sys
from typing import Any
from urllib.parse import urlencode

from clickcast import __version__ as CLICKCAST_VERSION
from clickcast.feedback.models import Report, StepReport
from clickcast.feedback.pointers import REPO_URL
from clickcast.feedback.redact import redact_report

__all__ = ["build_agent_report", "prefilled_issue_url", "render_diagnostics"]

REPORT_TEMPLATE = f"{REPO_URL}/issues/new?template=ai-agent-report.yml"

_SCHEMA_VERSION = 1


def build_agent_report(
    report: Report,
    *,
    redact: bool = True,
    environment_note: str | None = None,
    command_or_api_call: str | None = None,
) -> dict[str, Any]:
    """Assemble the Track-C payload (see ``docs/agent-report-schema/v1.json``).

    ``report`` is the loaded sidecar. When ``redact`` is set (the default),
    URLs / selectors / visible text in the sidecar excerpt are sanitized. The
    output dict is JSON-safe (no non-primitives at any depth).
    """
    failed = _first_failed_step(report)
    excerpt = _build_excerpt(report, failed)
    if redact:
        excerpt = redact_report(excerpt)
    call = command_or_api_call or _infer_command(report)
    expected, actual = _infer_narrative(report, failed)
    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "clickcast_version": report.clickcast_version,
        "python": platform.python_version(),
        "os": platform.system(),
        "command_or_api_call": call,
        "expected": expected,
        "actual": actual,
        "reproduction": {"kind": "cli", "content": call},
        "sidecar_excerpt": excerpt,
        "redacted": redact,
    }
    try:
        import playwright  # noqa: F401
        from playwright import __version__ as pw_version  # type: ignore[attr-defined]

        payload["playwright_version"] = pw_version
    except Exception:  # pragma: no cover — playwright is a hard dep
        pass
    if environment_note:
        payload["environment_note"] = environment_note
    return payload


def prefilled_issue_url(payload: dict[str, Any]) -> str:
    """Return a ``github.com/…/issues/new`` URL with title + body prefilled
    from ``payload`` (as built by :func:`build_agent_report`)."""
    title = _title(payload)
    body = _body(payload)
    query = urlencode({"template": "ai-agent-report.yml", "title": title, "body": body})
    return f"{REPO_URL}/issues/new?{query}"


def render_diagnostics(payload: dict[str, Any]) -> str:
    """Human-readable summary — printed on stdout alongside the URL."""
    lines: list[str] = []
    lines.append(
        f"clickcast {payload['clickcast_version']} · python {payload['python']} · {payload['os']}"
    )
    if pw := payload.get("playwright_version"):
        lines.append(f"playwright {pw}")
    lines.append(f"command: {payload['command_or_api_call']}")
    lines.append(f"expected: {payload['expected']}")
    lines.append(f"actual:   {payload['actual']}")
    excerpt = payload["sidecar_excerpt"]
    if failed := excerpt.get("failed_step"):
        lines.append(
            f"failed step #{failed.get('index')}: "
            f"{failed.get('action')} → {failed.get('error') or failed.get('status')}"
        )
        if frames := failed.get("frames"):
            joined = ", ".join(frames[:3]) + (
                f", +{len(frames) - 3} more" if len(frames) > 3 else ""
            )
            lines.append(f"  frames: {joined}")
    if warns := excerpt.get("warnings"):
        lines.append(f"warnings: {len(warns)}")
    if errs := excerpt.get("errors"):
        lines.append(f"errors: {len(errs)}")
    if graph := excerpt.get("graph"):
        lines.append(f"graph: {graph}")
    lines.append(f"redacted: {payload['redacted']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------


def _first_failed_step(report: Report) -> StepReport | None:
    for step in report.steps:
        if step.status != "ok":
            return step
    return None


def _build_excerpt(report: Report, failed: StepReport | None) -> dict[str, Any]:
    excerpt: dict[str, Any] = {
        "url": report.url,
        "engine": report.engine,
        "viewport": list(report.viewport),
        "step_count": len(report.steps),
    }
    if failed is not None:
        excerpt["failed_step"] = failed.model_dump(mode="json")
    if report.warnings:
        excerpt["warnings"] = list(report.warnings)
    if report.errors:
        excerpt["errors"] = list(report.errors)
    if summary := _graph_summary(report):
        excerpt["graph"] = summary
    return excerpt


def _graph_summary(report: Report) -> str | None:
    """One-line ``N pages, M components, K navigation edges`` — v2 additive.

    Rendered into the excerpt AND the human-readable diagnostics when the
    sidecar carries a graph with at least one node. Absent for v1 sidecars
    and for tours that produced no ``page_state.url_after`` values.
    """
    graph = report.graph
    if graph is None or not graph.nodes:
        return None
    pages = sum(1 for n in graph.nodes if getattr(n, "kind", None) == "page")
    components = sum(1 for n in graph.nodes if getattr(n, "kind", None) == "component")
    edges = len(graph.edges)
    return f"{pages} pages, {components} components, {edges} navigation edges"


def _infer_command(report: Report) -> str:
    """Best-effort reconstruction of the command that produced ``report``."""
    if report.url:
        return f"clickcast auto {report.url} --out <path>"
    return "clickcast auto <url> --out <path>"


def _infer_narrative(report: Report, failed: StepReport | None) -> tuple[str, str]:
    if failed is None:
        expected = "A GIF + sidecar; every step OK; discovery finds ≥ 1 element."
        if report.errors:
            actual = f"Sidecar carried {len(report.errors)} error(s) at tour level."
        elif report.warnings:
            actual = f"Sidecar carried {len(report.warnings)} warning(s); no step failure."
        else:
            actual = "Nothing obviously wrong — filed for review."
        return expected, actual
    expected = f"Step {failed.index} ({failed.action}) succeeds."
    err = failed.error or f"status={failed.status}"
    actual = f"Step {failed.index} ({failed.action}) — {err}"
    return expected, actual


def _title(payload: dict[str, Any]) -> str:
    excerpt = payload["sidecar_excerpt"]
    if failed := excerpt.get("failed_step"):
        return f"[agent-report] step {failed['index']} {failed['action']} — {failed.get('status', 'failed')}"
    return "[agent-report] "


def _body(payload: dict[str, Any]) -> str:
    excerpt_json = json.dumps(payload["sidecar_excerpt"], indent=2)
    lines = [
        "> Filed via `clickcast report-bug`.",
        "",
        f"**clickcast**: {payload['clickcast_version']}",
    ]
    if pw := payload.get("playwright_version"):
        lines.append(f"**playwright**: {pw}")
    lines.extend(
        [
            f"**python**: {payload['python']}",
            f"**os**: {payload['os']}",
            "",
            "### Command or API call",
            "",
            "```shell",
            payload["command_or_api_call"],
            "```",
            "",
            "### Expected",
            payload["expected"],
            "",
            "### Actual",
            payload["actual"],
            "",
            "### Reproduction",
            f"kind: `{payload['reproduction']['kind']}`",
            "",
            "```shell",
            payload["reproduction"]["content"],
            "```",
            "",
            "### Sidecar excerpt",
            "",
            "```json",
            excerpt_json,
            "```",
            "",
            f"redacted: **{payload['redacted']}**",
        ]
    )
    if note := payload.get("environment_note"):
        lines.extend(["", "### Environment note", note])
    return "\n".join(lines)


def _open_url(url: str) -> None:
    """xdg-open / open / start the given URL."""
    import subprocess

    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    elif sys.platform == "win32":  # pragma: no cover — unix CI
        subprocess.run(["cmd", "/c", "start", "", url], check=False, shell=False)
    else:
        subprocess.run(["xdg-open", url], check=False)


# Keep the version symbol used in the docstring alongside the code.
assert CLICKCAST_VERSION
