"""Auto-discovery — find `worth clicking` interactive elements on a page."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, cast

from clickcast.core.session import Session

__all__ = ["Element", "discover"]


@dataclass(slots=True, frozen=True)
class Element:
    selector: str
    role: str
    text: str
    bbox: tuple[int, int, int, int]  # x, y, width, height
    score: int
    source: str  # "dom-heuristic" | "ax-tree"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        return d


_DISCOVERY_JS = r"""
() => {
  const candidateSelectors = [
    'button',
    'a[href]',
    '[role="button"]',
    '[role="tab"]',
    '[role="switch"]',
    '[role="checkbox"]',
    '[role="menuitem"]',
    '[role="link"]',
    'input[type="submit"]',
    'input[type="button"]',
    'select',
    'summary',
    '[data-testid]',
  ];

  const seen = new Set();
  const out = [];

  for (const sel of candidateSelectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue;
      seen.add(el);

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

      // Accessible name (a very small subset of the WAI algorithm — enough for MVP)
      let name = (el.getAttribute('aria-label') || '').trim();
      if (!name) {
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
          const ids = labelledBy.split(/\s+/);
          const parts = ids
            .map((id) => document.getElementById(id))
            .filter(Boolean)
            .map((node) => (node.innerText || node.textContent || '').trim());
          name = parts.join(' ').trim();
        }
      }
      if (!name) name = (el.getAttribute('title') || '').trim();
      if (!name) name = (el.getAttribute('alt') || '').trim();
      if (!name) {
        name = (el.innerText || el.textContent || '').trim();
      }
      // Truncate long text (accessible names are short by convention)
      if (name.length > 60) name = name.slice(0, 60);

      // Role — explicit ARIA role wins, otherwise implicit from tag
      let role = el.getAttribute('role') || '';
      if (!role) {
        const tag = el.tagName.toLowerCase();
        if (tag === 'button') role = 'button';
        else if (tag === 'a') role = 'link';
        else if (tag === 'select') role = 'combobox';
        else if (tag === 'summary') role = 'button';
        else if (tag === 'input') {
          const t = (el.getAttribute('type') || 'text').toLowerCase();
          role = (t === 'submit' || t === 'button') ? 'button' : t;
        } else {
          role = tag;
        }
      }

      const inFooter = !!el.closest('footer');
      const ariaHidden = el.getAttribute('aria-hidden') === 'true';
      const cls = typeof el.className === 'string' ? el.className : '';
      const skipClass = /skip|nav-skip/i.test(cls);

      const idAttr = el.id || '';
      const testId = el.getAttribute('data-testid') || '';

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
        id: idAttr,
        testId,
        tagName: el.tagName.toLowerCase(),
      });
    }
  }
  return out;
}
"""


def _score(c: dict[str, Any]) -> int:
    score = 0
    if c["visible"] and c["inViewport"]:
        score += 2
    name = c.get("name") or ""
    if 2 <= len(name) <= 30:
        score += 1
    bbox = c["bbox"]
    area = bbox[2] * bbox[3]
    if area < 24 * 24:
        score -= 2
    if c["inFooter"] or c["ariaHidden"] or c["skipClass"]:
        score -= 1
    return score


def _quote(s: str) -> str:
    """Quote a string for use inside a Playwright selector value."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _pick_selector(c: dict[str, Any]) -> str:
    """Pick the most durable selector for this candidate.

    Priority: role=<role>[name="<name>"] → [data-testid=...] → #id → text="..." → tag.
    """
    role = c.get("role") or ""
    name = c.get("name") or ""
    if role and name:
        return f'role={role}[name="{_quote(name)}"]'
    test_id = c.get("testId") or ""
    if test_id:
        return f'[data-testid="{_quote(test_id)}"]'
    idx = c.get("id") or ""
    if idx:
        return f"#{idx}"
    if name:
        return f'text="{_quote(name)}"'
    return c.get("tagName") or "*"


def _dedup(elements: list[Element]) -> list[Element]:
    """Cluster dedup: same (role, text) → keep the highest-scored."""
    best: dict[tuple[str, str], Element] = {}
    order: list[tuple[str, str]] = []
    for el in elements:
        key = (el.role, el.text)
        if key not in best:
            best[key] = el
            order.append(key)
        elif el.score > best[key].score:
            best[key] = el
    return [best[k] for k in order]


async def _discover_on_page(session: Session, *, interactive: bool, limit: int) -> list[Element]:
    raw = cast(list[dict[str, Any]], await session.page.evaluate(_DISCOVERY_JS))
    elements: list[Element] = []
    for c in raw:
        if interactive and not c["visible"]:
            continue
        elements.append(
            Element(
                selector=_pick_selector(c),
                role=c["role"],
                text=c["name"],
                bbox=(c["bbox"][0], c["bbox"][1], c["bbox"][2], c["bbox"][3]),
                score=_score(c),
                source="dom-heuristic",
            )
        )
    elements = _dedup(elements)
    elements.sort(key=lambda e: (-e.score, e.bbox[1], e.bbox[0]))
    return elements[:limit]


async def discover(
    target: Session | str,
    *,
    interactive: bool = True,
    limit: int = 20,
) -> list[Element]:
    """Return a ranked list of interactive elements on the target page.

    ``target`` is either a URL (a temporary Session is created) or an already-
    open ``Session`` (its current page is inspected). ``interactive=True`` (the
    default) drops hidden elements; ``limit`` caps the result size.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if isinstance(target, str):
        async with Session() as sess:
            await sess.goto(target, wait="networkidle")
            return await _discover_on_page(sess, interactive=interactive, limit=limit)
    return await _discover_on_page(target, interactive=interactive, limit=limit)
