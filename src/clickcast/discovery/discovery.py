"""Auto-discovery — find `worth clicking` interactive elements on a page."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from clickcast.core.session import Session
from clickcast.discovery.accessibility import AccessibleElement, capture_accessibility_batch

if TYPE_CHECKING:
    from clickcast.annotate.grid import GridConfig

__all__ = ["Element", "discover", "discover_with_accessibility"]


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


async def _disambiguate_selectors(session: Session, elements: list[Element]) -> list[Element]:
    """Append ``>> nth=0`` to any selector that matches multiple DOM elements.

    On real doc sites, our ``role=X[name=Y]`` selectors routinely match 2+
    elements (header nav + footer nav share the same label). Playwright's
    strict mode then refuses to click, and every attempt costs a full
    per-op timeout. Cheap fix: verify count per element; when >1, force the
    locator to pick the first match. Preserves accuracy on unique selectors
    (count check is a no-op) while eliminating strict-mode failures.

    Each element costs one extra ``Locator.count()`` — ~10ms on live pages.
    On a 30-element discovery pool that's ~300ms of overhead, dwarfed by
    the ~5s+ per-click timeout it prevents when collisions happen.
    """
    disambiguated: list[Element] = []
    for el in elements:
        # `nth=` already present (unlikely but defensive) or selector uses an
        # id / testid (inherently unique) → skip the check.
        if " >> nth=" in el.selector or el.selector.startswith(("#", "[data-testid=")):
            disambiguated.append(el)
            continue
        try:
            count = await session.locator(el.selector).count()
        except Exception:
            # Malformed selector or Playwright error — leave as-is; the
            # downstream click will surface the real error.
            disambiguated.append(el)
            continue
        if count > 1:
            disambiguated.append(replace(el, selector=f"{el.selector} >> nth=0"))
        else:
            disambiguated.append(el)
    return disambiguated


async def _discover_on_page(session: Session, *, interactive: bool, limit: int) -> list[Element]:
    raw = cast(list[dict[str, Any]], await session.evaluate(_DISCOVERY_JS))
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
    top = elements[:limit]
    return await _disambiguate_selectors(session, top)


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


async def discover_with_accessibility(
    target: Session | str,
    *,
    interactive: bool = True,
    limit: int = 20,
    grid: GridConfig | None = None,
) -> list[AccessibleElement]:
    """Like :func:`discover`, but each returned element also carries its
    Playwright accessibility node (role/name/state, #197) and, when
    ``grid`` is an enabled :class:`~clickcast.annotate.grid.GridConfig`,
    its top-left grid cell (#198) — one fused payload instead of a
    selector/bbox list a caller has to cross-reference against a separate
    accessibility-tree call.

    Runs :func:`discover` unchanged (same selector/score output, same
    ranking) then captures accessibility for the resulting pool via
    :func:`~clickcast.discovery.accessibility.capture_accessibility_batch`.
    """
    if isinstance(target, str):
        async with Session() as sess:
            await sess.goto(target, wait="networkidle")
            elements = await _discover_on_page(sess, interactive=interactive, limit=limit)
            return await capture_accessibility_batch(sess, elements, grid=grid)
    elements = await _discover_on_page(target, interactive=interactive, limit=limit)
    return await capture_accessibility_batch(target, elements, grid=grid)
