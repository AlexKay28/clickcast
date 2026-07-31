"""Pure builder that turns a step ledger into a :class:`Graph` — #29 Track C.

The graph is an app-shape summary an LLM can plan against: which pages
exist, which link points to which page, which stable components (nav,
footer, sidebar) recur across pages. Distinct from the step ledger,
which only records the specific action sequence that ran.

Kept deliberately narrow in v2-first:

- Only ``PageNode`` extraction — one node per distinct ``url_after``.
- Only ``transition_kind: "navigation"`` edges — click that changed URL.
- ``ComponentNode`` list ships empty. The landmark-detection pass
  (fingerprint role + aria-label + bbox on each frame) is a substantive
  feature on its own and deferred to a follow-up. The dedup helper
  :func:`dom_signature` is shipped so the follow-up can plug straight in.

``reveal`` / ``dismiss`` transitions (modal open/close without URL change)
need DOM diffing across step boundaries and are similarly deferred.

Consumers should treat the graph as best-effort — a bad build must never
prevent the sidecar from writing (see the try/except in
:meth:`clickcast.feedback.builder.ReportBuilder.build`).
"""

from __future__ import annotations

import hashlib

from clickcast.feedback.models import Edge, Graph, PageNode, StepReport

__all__ = ["build_graph", "dom_signature"]


def build_graph(steps: list[StepReport]) -> Graph | None:
    """Assemble a :class:`Graph` from an ordered ``StepReport`` sequence.

    Returns ``None`` for an empty tour or one that never produced a
    ``page_state.url_after`` — keeps sidecars smaller when there's nothing
    to say. Otherwise emits one ``PageNode`` per distinct URL (in
    first-seen order) and one ``Edge`` per URL transition.

    Deterministic: same input → same graph. IDs are ``"n{N}"`` numbered
    by first-appearance step index so downstream diffs stay readable.
    """
    if not steps:
        return None

    # url -> (id, first_step, last_step). Preserve insertion order.
    pages: dict[str, tuple[str, int, int]] = {}
    edges: list[Edge] = []
    prev_url: str | None = None

    for step in steps:
        url = _url_for(step)
        if not url:
            continue

        if url not in pages:
            node_id = f"n{len(pages) + 1}"
            pages[url] = (node_id, step.index, step.index)
        else:
            node_id, first_seen, _ = pages[url]
            pages[url] = (node_id, first_seen, step.index)

        if prev_url is not None and url != prev_url:
            from_id, _, _ = pages[prev_url]
            to_id, _, _ = pages[url]
            edges.append(
                Edge.model_validate(
                    {
                        "from": from_id,
                        "to": to_id,
                        "via_step": step.index,
                        "selector": _selector_for(step),
                        "transition_kind": "navigation",
                    }
                )
            )
        prev_url = url

    if not pages:
        return None

    nodes: list[PageNode] = [
        PageNode(
            id=node_id,
            url=url,
            title=_title_for(steps, url),
            first_seen_step=first_step,
            last_seen_step=last_step,
        )
        for url, (node_id, first_step, last_step) in pages.items()
    ]

    return Graph(nodes=list(nodes), edges=edges)


def dom_signature(role: str, aria_label: str, bbox: tuple[int, int, int, int]) -> str:
    """Deterministic 16-char fingerprint for a landmark subtree.

    Coarse-bucket the bbox at 64px so a nav that shifts a few pixels
    between page loads still dedupes, while a genuinely relocated sidebar
    (moved from left to right of the viewport) gets a distinct signature.
    """
    x, y, w, h = bbox
    bucket = (
        round(x / 64),
        round(y / 64),
        round(w / 64),
        round(h / 64),
    )
    payload = f"{role}|{aria_label}|{bucket}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------


def _url_for(step: StepReport) -> str:
    """``page_state.url_after`` if the collector captured one, else empty."""
    if step.page_state is None:
        return ""
    return step.page_state.url_after or ""


def _title_for(steps: list[StepReport], url: str) -> str:
    """First non-empty ``page_state.title`` recorded for ``url`` in order."""
    for step in steps:
        if step.page_state is None:
            continue
        if step.page_state.url_after == url and step.page_state.title:
            return step.page_state.title
    return ""


def _selector_for(step: StepReport) -> str | None:
    """Best-effort ``args.selector`` extraction — many actions carry one."""
    sel = step.args.get("selector") if isinstance(step.args, dict) else None
    if isinstance(sel, str) and sel:
        return sel
    return None
