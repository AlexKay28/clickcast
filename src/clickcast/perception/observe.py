"""Token-bounded page snapshots with stable opaque refs (#263).

``observe()`` is the agent-facing perception channel: one compact JSON object
describing what an agent can act on, without an annotated PNG and without
raw selectors. It ships::

    {"schema": "observe/1", "url": "...", "title": "...",
     "viewport": [1280, 800],
     "nodes": [{"ref": "e3", "role": "button", "name": "Buy now",
                "bbox": [890, 612, 120, 40],
                "state": {"disabled": false}}],
     "text_hash": "a1b2c3d4e5f60718",
     "truncated": {"omitted": 6, "total": 9},
     "tokens": {"budget": 800, "estimated": 312},
     "removed": []}

Ranking
-------
interactive role → visible + fully in viewport → the existing
:func:`clickcast.discovery.discovery._score` → y, then x. Names are truncated
to 40 characters for the wire; at most :data:`MAX_NODES` nodes are shipped,
then the budget loop drops the lowest-ranked nodes one by one until both the
token budget and the character ceiling in
:mod:`clickcast.perception.tokens` hold. ``truncated.total`` is the number of
visible interactive candidates found; ``truncated.omitted`` is
``total - len(nodes)``, so the two always add up.

Refs
----
Each node carries a session-scoped opaque ``ref`` from
:mod:`clickcast.perception.refs`. Identity is role + accessible name for
unique labels, tie-broken by the 64px bbox bucket (plus a document-order
ordinal) only when labels collide, so refs stay stable across repeated
observations and across in-page layout shifts. Refs that disappeared from
the DOM are reported once in the top-level ``"removed"`` list (see the refs
module for the exact transition rule).

Playwright's native ``page.get_by_ref()``
----------------------------------------
Playwright 1.64 added a native ref fast-path. It is **not** used here: the
snapshot API is newer than clickcast's supported ``playwright>=1.49`` floor,
and refs must remain meaningful for the whole live session. The current
registry (role + name + 64px bucket → ``role=... >> nth=N``) is the portable
implementation; switching to native refs later is a contained change behind
the same ``ref=eN`` handles and is documented as a future fast-path, not a
requirement.

This module does not import Playwright types; it talks to the
:class:`clickcast.core.session.Session` seam only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any, cast

from clickcast.core.session import Session
from clickcast.discovery.discovery import _pick_selector, _score
from clickcast.perception.refs import (
    RefCandidate,
    RefRegistry,
    registry_for,
)
from clickcast.perception.tokens import (
    DEFAULT_TOKEN_BUDGET,
    compact_json,
    estimate_tokens,
    fits_budget,
)

__all__ = ["MAX_NODES", "SCHEMA", "observe"]

SCHEMA = "observe/1"

#: Hard cap on shipped nodes per snapshot (issue #263).
MAX_NODES = 20

#: Wire-level name truncation. Identity uses the 60-char DOM name; only the
#: published node name is shortened.
NAME_LIMIT = 40

#: Roles that count as "interactive" for the first ranking key. The candidate
#: query already targets interactive elements, so this is mostly a stable
#: ordering guard for role values that sneak in via ``[data-testid]``.
_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "link",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "radio",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
    }
)

# Mirrors discovery's candidate pool (plus form fields, which an observer
# cares about just as much as a click target) and additionally reports the
# disabled state the ``observe/1`` node schema carries. Kept as its own JS
# because discovery's snapshot has no state and drops hidden elements, but
# the role/name/bbox extraction follows the same rules deliberately.
_OBSERVE_JS = r"""
() => {
  const candidateSelectors = [
    'button',
    'a[href]',
    '[role="button"]',
    '[role="tab"]',
    '[role="switch"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="menuitem"]',
    '[role="menuitemcheckbox"]',
    '[role="menuitemradio"]',
    '[role="link"]',
    '[role="combobox"]',
    '[role="textbox"]',
    '[role="searchbox"]',
    '[role="option"]',
    '[role="slider"]',
    '[role="spinbutton"]',
    'input:not([type="hidden"])',
    'textarea',
    'select',
    'summary',
    '[data-testid]',
    '[contenteditable="true"]',
  ];

  const seen = new Set();
  const nodes = [];
  for (const sel of candidateSelectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (!seen.has(el)) {
        seen.add(el);
        nodes.push(el);
      }
    }
  }
  // Document order — the registry's duplicate ordinal relies on it.
  nodes.sort((a, b) =>
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1
  );

  const out = [];
  for (const el of nodes) {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const visible = (
      style.display !== 'none' &&
      style.visibility !== 'hidden' &&
      parseFloat(style.opacity || '1') > 0.05 &&
      rect.width > 0 &&
      rect.height > 0
    );
    const inViewport = (
      rect.top >= 0 && rect.left >= 0 &&
      rect.bottom <= (window.innerHeight || 0) &&
      rect.right <= (window.innerWidth || 0)
    );

    let name = (el.getAttribute('aria-label') || '').trim();
    if (!name) {
      const labelledBy = el.getAttribute('aria-labelledby');
      if (labelledBy) {
        const ids = labelledBy.split(/\s+/);
        name = ids
          .map((id) => document.getElementById(id))
          .filter(Boolean)
          .map((node) => (node.innerText || node.textContent || '').trim())
          .join(' ')
          .trim();
      }
    }
    if (!name && el.labels && el.labels.length) {
      name = Array.from(el.labels)
        .map((label) => (label.innerText || label.textContent || '').trim())
        .join(' ')
        .trim();
    }
    if (!name) name = (el.getAttribute('title') || '').trim();
    if (!name) name = (el.getAttribute('alt') || '').trim();
    if (!name) name = (el.getAttribute('placeholder') || '').trim();
    if (!name) name = (el.innerText || el.textContent || '').trim();
    if (name.length > 60) name = name.slice(0, 60);

    let role = el.getAttribute('role') || '';
    if (!role) {
      const tag = el.tagName.toLowerCase();
      if (tag === 'button') role = 'button';
      else if (tag === 'a') role = 'link';
      else if (tag === 'select') role = 'combobox';
      else if (tag === 'summary') role = 'button';
      else if (tag === 'textarea') role = 'textbox';
      else if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'submit' || t === 'button' || t === 'reset' || t === 'image') role = 'button';
        else if (t === 'checkbox') role = 'checkbox';
        else if (t === 'radio') role = 'radio';
        else if (t === 'range') role = 'slider';
        else if (t === 'number') role = 'spinbutton';
        else if (t === 'search') role = 'searchbox';
        else role = 'textbox';
      } else {
        role = tag;
      }
    }

    const inFooter = !!el.closest('footer');
    const ariaHidden = el.getAttribute('aria-hidden') === 'true';
    const cls = typeof el.className === 'string' ? el.className : '';
    const skipClass = /skip|nav-skip/i.test(cls);

    out.push({
      role,
      name,
      bbox: [
        Math.round(rect.left),
        Math.round(rect.top),
        Math.round(rect.width),
        Math.round(rect.height),
      ],
      visible,
      inViewport,
      inFooter,
      ariaHidden,
      skipClass,
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      id: el.id || '',
      testId: el.getAttribute('data-testid') || '',
      tagName: el.tagName.toLowerCase(),
    });
  }

  return {
    viewport: [window.innerWidth || 0, window.innerHeight || 0],
    text: (document.body && document.body.innerText) || '',
    candidates: out,
  };
}
"""


def _rank_key(candidate: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """interactive role → visible+in viewport → score → y, then x."""
    bbox = candidate["bbox"]
    return (
        0 if candidate["role"] in _INTERACTIVE_ROLES else 1,
        0 if (candidate["visible"] and candidate["inViewport"]) else 1,
        -_score(cast("dict[str, Any]", candidate)),
        int(bbox[1]),
        int(bbox[0]),
    )


def _selectors_for(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    """Build one selector per candidate, adding ``>> nth=`` for duplicates.

    ``role=button[name="Home"]`` can match both a header and a footer link.
    Playwright's strict mode then refuses to act; the shipped disambiguation
    (``discovery._disambiguate_selectors``) uses ``>> nth=``, so observe
    applies the same convention up front. Ids and test ids are inherently
    unique and never get an nth suffix.
    """
    totals: dict[tuple[str, str], int] = {}
    for candidate in candidates:
        key = (candidate["role"], candidate["name"])
        totals[key] = totals.get(key, 0) + 1

    positions: dict[tuple[str, str], int] = {}
    selectors: list[str] = []
    for candidate in candidates:
        key = (candidate["role"], candidate["name"])
        position = positions.get(key, 0)
        positions[key] = position + 1
        selector = _pick_selector(cast("dict[str, Any]", candidate))
        if totals[key] > 1 and not selector.startswith(("#", "[data-testid=")):
            selector = f"{selector} >> nth={position}"
        selectors.append(selector)
    return selectors


def _node(candidate: Mapping[str, Any], ref: str) -> dict[str, Any]:
    bbox = candidate["bbox"]
    return {
        "ref": ref,
        "role": candidate["role"],
        "name": str(candidate["name"])[:NAME_LIMIT],
        "bbox": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
        "state": {"disabled": bool(candidate["disabled"])},
    }


def _serialize(payload: dict[str, Any]) -> tuple[str, int]:
    """Serialize ``payload`` with a fixed-point ``tokens.estimated`` value.

    The estimate is part of the payload, so setting it can change its own
    character count (``0`` → ``312`` adds two chars). Iterate until the
    serialized bytes and the reported estimate agree; the test asserts
    ``estimate_tokens(compact_json(payload)) == payload["tokens"]["estimated"]``.
    """
    tokens = cast("dict[str, int]", payload["tokens"])
    tokens["estimated"] = 0
    text = compact_json(payload)
    estimate = estimate_tokens(text)
    for _ in range(8):
        if estimate == tokens["estimated"]:
            break
        tokens["estimated"] = estimate
        text = compact_json(payload)
        estimate = estimate_tokens(text)
    return text, estimate


def _fit(payload: dict[str, Any], *, token_budget: int) -> dict[str, Any]:
    """Drop lowest-ranked nodes until the budget and char ceiling hold."""
    nodes = cast("list[dict[str, Any]]", payload["nodes"])
    total = int(cast("dict[str, int]", payload["truncated"])["total"])
    while True:
        cast("dict[str, int]", payload["truncated"])["omitted"] = total - len(nodes)
        text, _estimate = _serialize(payload)
        if fits_budget(text, token_budget=token_budget) or not nodes:
            # A budget smaller than the metadata envelope cannot be met; the
            # snapshot is still returned rather than raising, with a truthful
            # estimate so the caller can see it. Normal budgets never hit this.
            return payload
        nodes.pop()


async def observe(
    session: Session,
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    registry: RefRegistry | None = None,
) -> dict[str, Any]:
    """Return one ranked, token-bounded snapshot of ``session``'s page.

    Pass ``registry`` only if you own a registry explicitly; the default is
    the session-scoped registry from
    :func:`clickcast.perception.refs.registry_for`, which is what makes refs
    stable across consecutive calls.
    """
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")

    raw = cast("dict[str, Any]", await session.evaluate(_OBSERVE_JS))
    candidates = cast("list[dict[str, Any]]", raw.get("candidates") or [])
    selectors = _selectors_for(candidates)

    reg = registry if registry is not None else registry_for(session)
    ref_candidates = [
        RefCandidate(
            role=str(candidate["role"]),
            name=str(candidate["name"]),
            bbox=(
                int(candidate["bbox"][0]),
                int(candidate["bbox"][1]),
                int(candidate["bbox"][2]),
                int(candidate["bbox"][3]),
            ),
            selector=selectors[index],
            visible=bool(candidate["visible"]),
        )
        for index, candidate in enumerate(candidates)
    ]
    refs = reg.observe(ref_candidates)

    visible = [
        (index, candidate) for index, candidate in enumerate(candidates) if candidate["visible"]
    ]
    visible.sort(key=lambda pair: _rank_key(pair[1]))

    nodes: list[dict[str, Any]] = []
    for index, candidate in visible[:MAX_NODES]:
        ref = refs.get(index)
        if ref is None:  # pragma: no cover — every visible candidate is registered
            continue
        nodes.append(_node(candidate, ref))

    viewport_raw = cast("list[Any]", raw.get("viewport") or [0, 0])
    viewport = [int(viewport_raw[0]), int(viewport_raw[1])] if len(viewport_raw) >= 2 else [0, 0]
    body_text = str(raw.get("text") or "")

    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "url": session.url_now,
        "title": await session.title(),
        "viewport": viewport,
        "nodes": nodes,
        "text_hash": hashlib.sha256(body_text.encode("utf-8")).hexdigest()[:16],
        "truncated": {"omitted": max(0, len(visible) - len(nodes)), "total": len(visible)},
        "tokens": {"budget": token_budget, "estimated": 0},
        "removed": reg.removed,
    }
    return _fit(payload, token_budget=token_budget)
