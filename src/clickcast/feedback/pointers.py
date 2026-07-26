"""Build the sidecar's optional ``feedback`` pointer block.

The block is metadata about the reporting mechanism itself (where to file
issues, how the maintainers want them framed) — not about the tour. Attached
by :func:`clickcast.feedback.write` when the caller passes ``with_feedback=True``.

See #40 (AI-agent feedback loop) for the broader motivation.
"""

from __future__ import annotations

from urllib.parse import urlencode

from clickcast.feedback.models import Feedback, FeedbackTemplate, Report

__all__ = ["REPO_URL", "build_feedback"]

REPO_URL = "https://github.com/AlexKay28/clickcast"

_MESSAGE = (
    "clickcast is early — bug reports and ideas that would make it better "
    "are very welcome. If something in this reel or sidecar looks wrong, or "
    "you have a concrete idea for what would help, please open an issue on "
    "GitHub. Include a short problem description and, if you can, a "
    "possible resolution plan (a sketch is fine — even naming the file you "
    "think needs to change is useful)."
)

_TEMPLATE = FeedbackTemplate(
    problem="What went wrong, or what feels off about the reel / sidecar?",
    resolution_plan="Concrete steps you think would fix it — files to touch, config to add, tests to write.",
)


def build_feedback(report: Report) -> Feedback:
    """Assemble a :class:`Feedback` pointer, prefilling the new-issue URL
    with context lifted from ``report`` (clickcast version, engine, viewport,
    target URL) so a filed issue arrives with the environment already noted.
    """
    body_lines = [
        "**Problem**",
        _TEMPLATE.problem,
        "",
        "**Resolution plan**",
        _TEMPLATE.resolution_plan,
        "",
        "---",
        "",
        "**Environment (autofilled from sidecar)**",
        f"- clickcast: {report.clickcast_version}",
        f"- engine: {report.engine}",
        f"- viewport: {report.viewport[0]}x{report.viewport[1]}",
    ]
    if report.url:
        body_lines.append(f"- target url: {report.url}")
    body = "\n".join(body_lines)
    title = "[feedback] "
    query = urlencode({"title": title, "body": body})
    new_issue_url = f"{REPO_URL}/issues/new?{query}"
    return Feedback(
        message=_MESSAGE,
        repo=REPO_URL,
        issues_url=f"{REPO_URL}/issues",
        new_issue_url=new_issue_url,
        template=_TEMPLATE,
    )
