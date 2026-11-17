"""Structured per-action page deltas — "what changed?" (#264).

``observe()`` answers *what can I act on?* and ``settle()`` answers *has the
page stopped changing?*. Neither answers the question an agent asks right
after an action: **what did that action actually do?** This module diffs a
before/after observation pair (plus the collector's per-step console/network
state) into one compact, token-bounded ``StepDelta``::

    {"url": "https://example.com/tabs.html", "title": "Tabs",
     "added": [{"ref": "e7", "role": "button", "name": "Details"}],
     "removed": [{"ref": "e2", "role": "button", "name": "Open menu"}],
     "text_changes": ["+ Details content — hidden until Details is clicked."],
     "console_errors": ["Uncaught TypeError: ..."],
     "network_failed": ["GET https://example.com/api/items"],
     "layout_shift": true,
     "settled": {"ok": true, "ms": 812}}

Differences that are not real reactions
---------------------------------------
Node identity is the same 64px-quantized signature the ref registry uses
(:func:`clickcast.feedback.graph.dom_signature`, imported — not copied), so a
node that merely shifted a few pixels keeps its identity and never churns
``added`` + ``removed``. Independently, a bbox delta under
:data:`BBOX_JITTER_PX` on every coordinate is treated as visual noise and does
not set ``layout_shift``. A remounted but identical node has the same
signature on both sides, so it is not reported either.

Text changes
------------
``observe/1`` only ships a whole-body ``text_hash``. To make a revealed panel
legible as a *delta* (the panel body is not an interactive candidate), a
capture also reads a bounded list of visible text blocks from the page. The
diff reports blocks that appeared (``"+ ..."``) and disappeared (``"- ..."``),
truncated to :data:`TEXT_LIMIT` characters, most-actionable first.

Budgets
-------
Every list is capped on construction (5 items; 3 for ``console_errors`` /
``network_failed``) and ranked before the cap is applied. The whole payload
is then fitted to :data:`DELTA_MAX_TOKENS` estimated tokens by the shipped
:func:`clickcast.perception.tokens.estimate_tokens` / ``compact_json`` pair —
in production code, never in the test only. Empty blocks are omitted.

``delta`` does not import Playwright types; it talks to the
:class:`clickcast.core.session.Session` seam, like the rest of
``perception/``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from clickcast.core.session import Session
from clickcast.feedback.graph import dom_signature
from clickcast.perception.observe import observe
from clickcast.perception.settle import SettleResult
from clickcast.perception.tokens import (
    DEFAULT_TOKEN_BUDGET,
    compact_json,
    estimate_tokens,
)

__all__ = [
    "BBOX_JITTER_PX",
    "DELTA_MAX_TOKENS",
    "MAX_ERRORS",
    "MAX_ITEMS",
    "Observation",
    "capture",
    "diff_observations",
    "fit_delta",
]

#: Hard cap on the estimated-token size of one serialized delta (#264).
DELTA_MAX_TOKENS = 300

#: Cap on ``added`` / ``removed`` / ``text_changes`` entries.
MAX_ITEMS = 5

#: Cap on ``console_errors`` / ``network_failed`` entries.
MAX_ERRORS = 3

#: Bbox movement (in px, on any coordinate) below this is visual noise, not a
#: layout shift. Matches the "a few pixels of sub-pixel/rounding jitter is not
#: a reaction" rule the ref identity already implies at 64px granularity.
BBOX_JITTER_PX = 4

#: Wire-level truncation for names and text-change entries.
NAME_LIMIT = 40
TEXT_LIMIT = 80

#: Upper bound on the raw text-block probe, before truncation/diffing.
_TEXT_PROBE_LIMIT = 300

# Reads visible text blocks (elements with their own direct text) so a
# revealed panel — not an interactive candidate, hence absent from
# ``observe/1``'s ``nodes`` — still shows up as a text change. Bounded and
# exception-free by construction: no page data means an empty list, never a
# raise into the action path.
_TEXT_JS = r"""
() => {
  const root = document.body;
  if (!root) return [];
  const out = [];
  const seen = new Set();
  let scanned = 0;
  for (const el of root.querySelectorAll('*')) {
    if (scanned >= 400 || out.length >= 300) break;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (parseFloat(style.opacity || '1') <= 0.05) continue;
    let text = '';
    for (const child of el.childNodes) {
      if (child.nodeType === 3) text += child.nodeValue;
    }
    text = text.replace(/\s+/g, ' ').trim();
    if (!text) continue;
    scanned += 1;
    if (text.length > 120) text = text.slice(0, 120);
    if (seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}
"""


@dataclass(frozen=True, slots=True)
class Observation:
    """One side of a delta: the shipped ``observe/1`` payload + text blocks."""

    url: str
    title: str
    viewport: tuple[int, int]
    nodes: tuple[dict[str, Any], ...]
    texts: tuple[str, ...]
    removed: tuple[str, ...] = ()


def _as_observation(payload: dict[str, Any], raw_texts: Any) -> Observation:
    viewport_raw = payload.get("viewport") or [0, 0]
    viewport = (
        (
            int(viewport_raw[0]),
            int(viewport_raw[1]),
        )
        if len(viewport_raw) >= 2
        else (0, 0)
    )
    nodes = tuple(dict(node) for node in cast("list[dict[str, Any]]", payload.get("nodes") or []))
    texts: list[str] = []
    if isinstance(raw_texts, list):
        for item in raw_texts:
            if isinstance(item, str) and item:
                texts.append(item[:TEXT_LIMIT])
    return Observation(
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or ""),
        viewport=viewport,
        nodes=nodes,
        texts=tuple(texts),
        removed=tuple(cast("list[str]", payload.get("removed") or [])),
    )


async def capture(session: Session, *, token_budget: int = DEFAULT_TOKEN_BUDGET) -> Observation:
    """Snapshot one side of a delta (``observe/1`` + bounded text blocks)."""
    payload = await observe(session, token_budget=token_budget)
    raw_texts = await session.evaluate(_TEXT_JS)
    return _as_observation(payload, raw_texts)


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------


def _bbox(node: dict[str, Any]) -> tuple[int, int, int, int]:
    raw = node.get("bbox") or [0, 0, 0, 0]
    return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))


def _signature(node: dict[str, Any]) -> str:
    """64px-bucket identity — the exact fingerprint the ref registry uses."""
    return dom_signature(str(node.get("role") or ""), str(node.get("name") or ""), _bbox(node))


def _node_entry(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": str(node.get("ref") or ""),
        "role": str(node.get("role") or ""),
        "name": str(node.get("name") or "")[:NAME_LIMIT],
    }


def _in_viewport(node: dict[str, Any], viewport: tuple[int, int]) -> bool:
    width, height = viewport
    x, y, w, h = _bbox(node)
    return x >= 0 and y >= 0 and (width <= 0 or x + w <= width) and (height <= 0 or y + h <= height)


def _rank_nodes(nodes: Sequence[dict[str, Any]], viewport: tuple[int, int]) -> list[dict[str, Any]]:
    """Rank most-actionable first: in-viewport → y → x → name."""
    return sorted(
        nodes,
        key=lambda node: (
            0 if _in_viewport(node, viewport) else 1,
            _bbox(node)[1],
            _bbox(node)[0],
            str(node.get("name") or ""),
        ),
    )


def _moved(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """True when a shared-identity node moved beyond the jitter threshold."""
    return any(
        abs(a - b) >= BBOX_JITTER_PX for a, b in zip(_bbox(before), _bbox(after), strict=False)
    )


def _text_changes(before: Observation, after: Observation) -> list[str]:
    before_texts = set(before.texts)
    after_texts = set(after.texts)
    changed = [f"+ {text}" for text in after.texts if text not in before_texts]
    changed.extend(f"- {text}" for text in before.texts if text not in after_texts)
    return [entry[:TEXT_LIMIT] for entry in changed]


def _dedupe(items: Iterable[str]) -> list[str]:
    """Keep the last occurrence of each (most recent wins), order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for item in reversed([str(item) for item in items if item]):
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    out.reverse()
    return out


def _settled_payload(settled: SettleResult | None) -> dict[str, Any] | None:
    if settled is None:
        return None
    return {"ok": settled.ok, "ms": settled.ms}


def diff_observations(
    before: Observation,
    after: Observation,
    *,
    console_errors: Sequence[str] = (),
    network_failed: Sequence[str] = (),
    settled: SettleResult | None = None,
) -> dict[str, Any]:
    """Diff two :class:`Observation`s into a capped, budgeted delta payload."""
    pairs, added_nodes, removed_nodes = _match_nodes(before, after)
    layout_shift = any(_moved(before_node, after_node) for before_node, after_node in pairs)

    added = [_node_entry(node) for node in _rank_nodes(added_nodes, after.viewport)][:MAX_ITEMS]
    removed = [_node_entry(node) for node in _rank_nodes(removed_nodes, after.viewport)][:MAX_ITEMS]
    text_changes = _text_changes(before, after)[:MAX_ITEMS]
    errors = _dedupe(console_errors)[-MAX_ERRORS:]
    failed = _dedupe(network_failed)[-MAX_ERRORS:]

    payload: dict[str, Any] = {"url": after.url, "title": after.title}
    if added:
        payload["added"] = added
    if removed:
        payload["removed"] = removed
    if text_changes:
        payload["text_changes"] = text_changes
    if errors:
        payload["console_errors"] = errors
    if failed:
        payload["network_failed"] = failed
    if layout_shift:
        payload["layout_shift"] = True
    settled_payload = _settled_payload(settled)
    if settled_payload is not None:
        payload["settled"] = settled_payload
    return fit_delta(payload)


def _match_nodes(
    before: Observation, after: Observation
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair nodes across observations; return ``(pairs, added, removed)``.

    Identity is the 64px-bucket signature first. A survivor whose box moved
    across a bucket boundary (a menu opening above it, say) would otherwise
    churn ``added`` + ``removed``; the opaque ref is stable for the life of
    the session, so it is the fallback key for those leftovers. A genuinely
    remounted node keeps its signature and matches in the first pass.
    """
    after_nodes = list(after.nodes)
    signature_index: dict[str, list[int]] = {}
    for position, node in enumerate(after_nodes):
        signature_index.setdefault(_signature(node), []).append(position)

    used_after: set[int] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_before: list[dict[str, Any]] = []
    for node in before.nodes:
        candidates = [
            position
            for position in signature_index.get(_signature(node), [])
            if position not in used_after
        ]
        if candidates:
            used_after.add(candidates[0])
            pairs.append((node, after_nodes[candidates[0]]))
        else:
            unmatched_before.append(node)

    by_ref = {
        str(node.get("ref")): position
        for position, node in enumerate(after_nodes)
        if node.get("ref") and position not in used_after
    }
    still_removed: list[dict[str, Any]] = []
    for node in unmatched_before:
        match = by_ref.get(str(node.get("ref") or ""))
        if match is not None and match not in used_after:
            used_after.add(match)
            pairs.append((node, after_nodes[match]))
        else:
            still_removed.append(node)

    added = [node for position, node in enumerate(after_nodes) if position not in used_after]
    return pairs, added, still_removed


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------

_LIST_KEYS = ("added", "removed", "text_changes", "console_errors", "network_failed")


def fit_delta(payload: dict[str, Any], *, max_tokens: int = DELTA_MAX_TOKENS) -> dict[str, Any]:
    """Shrink ``payload`` in place until it fits ``max_tokens`` estimated tokens.

    Trimming order is deterministic and least-information-loss-first:
    overlong text-change strings are shortened, then the largest remaining
    list loses its lowest-ranked trailing entry, and only then are optional
    keys dropped entirely. ``url`` / ``title`` are the last resort.
    """
    while estimate_tokens(compact_json(payload)) > max_tokens:
        text_changes = cast("list[str]", payload.get("text_changes") or [])
        longest = max(text_changes, key=len, default="")
        if len(longest) > 24:
            index = text_changes.index(longest)
            text_changes[index] = longest[:24]
            continue

        populated = [key for key in _LIST_KEYS if payload.get(key)]
        if populated:
            biggest = max(populated, key=lambda key: len(payload[key]))
            cast("list[Any]", payload[biggest]).pop()
            if not payload[biggest]:
                del payload[biggest]
            continue

        # No optional lists left: truncate the fixed fields. This always makes
        # progress (and a delta that fits these limits is far below 300 tokens).
        title = str(payload.get("title") or "")
        url = str(payload.get("url") or "")
        if len(title) > 24:
            payload["title"] = title[:24]
            continue
        if len(url) > 48:
            payload["url"] = url[:48]
            continue
        break
    return payload
