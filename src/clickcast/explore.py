"""Explore engine — fast state-graph site exploration without a reel.

`clickcast auto --traversal bfs` explores *inside* a recording: every edge it
finds costs a screenshot, cursor interpolation, an annotation pass, and an
encode. An agent that only wants an app's **shape** — nodes, edges, what's
clickable where — pays for a movie it will never watch.

:func:`explore_site` reuses the same building blocks (``Session``,
``discover``, ``execute`` on ``GotoStep``/``ClickStep``) but skips the
``Recorder``, the annotator, and the encoder entirely, emitting a compact
``explore.json`` instead:

- one node per distinct page state (normalised-URL dedup, same keys as the
  auto queue),
- one edge per click that changed the URL (``transition_kind: "navigation"``,
  reusing the sidecar's :class:`~clickcast.feedback.models.Graph` models so
  consumers see a familiar shape),
- the top discoveries per visited page for selector-authoring.

Default traversal is BFS — site-map coverage is exactly what an agent
mapping an app wants. DFS is available for narrative-order walks.

The output is a *new* artifact (``schema_version: 1``, documented in
``docs/explore-schema.md``); it does not touch the reel sidecar or the
``assertions`` byte-stability contract.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import typer

from clickcast import __version__
from clickcast.core.actions import ClickStep, GotoStep, execute
from clickcast.core.session import Session
from clickcast.discovery import Element, discover
from clickcast.discovery.urlutil import is_same_origin, normalize_url
from clickcast.feedback.models import Edge, Graph, PageNode

__all__ = ["ExploreConfig", "explore_site"]


log = logging.getLogger("clickcast.explore")


# ---------------------------------------------------------------------------
# Tuned constants — mirror auto.py's values so the two engines feel the same.
# ---------------------------------------------------------------------------

# `page.go_back(wait_until="domcontentloaded", timeout=...)` after a same-origin
# navigation, so the next click lands on the page we were exploring.
_GOTO_BACK_TIMEOUT_MS = 5000

# Between clicks: let the DOM settle before reading the next URL. See auto.py
# PR #56 for the tuning story.
_INTER_CLICK_WAIT_S = 0.3

# Bail a page after N consecutive failed clicks — post-hydration DOM drift is
# the usual culprit. See auto.py PR #61.
_MAX_CONSECUTIVE_FAILURES = 3

# Discovery pool size. Exploration clicks one element at a time, but a
# generous pool keeps nav links in reach when early candidates fail.
_EXPLORE_DISCOVERY_POOL = 20


@dataclass(slots=True)
class ExploreConfig:
    """Typed inputs for a site exploration. Callers construct one and pass it
    to :func:`explore_site`.

    Fields group into:

    - **Target** — `url`, `out`.
    - **Traversal** — `max_steps`, `max_pages`, `max_duration`, `traversal`.
    - **Per-action tuning** — `click_timeout_ms`, `initial_wait`.
    - **Session** — `session_kwargs` (dict passed to :class:`Session`).
    - **Output** — `per_page_discoveries` (top-N elements pinned per page).
    """

    url: str
    out: str = "explore.json"
    max_steps: int = 15
    max_pages: int = 10
    max_duration: float = 120.0
    click_timeout_ms: int = 5000
    initial_wait: float = 1.0
    session_kwargs: dict[str, Any] = field(default_factory=dict)
    # Site-map coverage is the point of exploration, so BFS is the default —
    # the opposite default from `auto`, where DFS wins for narrative reels.
    traversal: str = "bfs"
    # How many of each page's discovered elements to pin in the output. The
    # full pool is still *clicked*; this only bounds the JSON's size.
    per_page_discoveries: int = 10


@dataclass(slots=True)
class _Visit:
    """Bookkeeping for one visited page, finalised into the payload later."""

    url: str
    ordinal: int
    title: str = ""
    discovered: list[Element] = field(default_factory=list)
    clicks_ok: int = 0
    clicks_failed: int = 0


def _die(msg: str, code: int = 1) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


async def explore_site(cfg: ExploreConfig) -> dict[str, Any]:
    """Explore ``cfg.url`` and return the exploration payload.

    Raises :class:`typer.Exit` on validation failures (matching the auto
    engine's behaviour). The caller (CLI) writes the payload to ``cfg.out``;
    the payload itself is plain JSON-serialisable data.

    Deterministic: same click outcomes → same nodes, edges, and ordering —
    wall-clock time is reported but excluded from identity.
    """
    if cfg.max_pages < 1:
        _die("explore: --max-pages must be >= 1")
    if cfg.max_duration <= 0:
        _die("explore: --max-duration must be > 0")
    if cfg.traversal not in ("dfs", "bfs"):
        _die(f"explore: --traversal must be 'dfs' or 'bfs', got {cfg.traversal!r}")

    started = time.monotonic()
    deadline = started + cfg.max_duration
    log.info(
        "starting explore: url=%s max_pages=%d max_steps=%d max_duration=%.1fs traversal=%s",
        cfg.url,
        cfg.max_pages,
        cfg.max_steps,
        cfg.max_duration,
        cfg.traversal,
    )

    # First-seen URL order drives node ids ("n1", "n2", ...), matching the
    # sidecar graph's numbering so consumers see a familiar shape.
    node_order: list[str] = [cfg.url]
    titles: dict[str, str] = {}
    visits: list[_Visit] = []
    # (from_url, to_url, visit_ordinal, selector) — one entry per click that
    # changed the URL, in discovery order. Converted to Edge models at the end.
    transitions: list[tuple[str, str, int, str]] = []

    async with Session(**cfg.session_kwargs) as sess:
        queue: deque[str] = deque([cfg.url])
        visited: set[str] = set()
        pages_visited = 0
        clicks_remaining = cfg.max_steps

        # Traversal policy mirrors run_tour: BFS pops FIFO, DFS pops LIFO.
        pop_next = queue.popleft if cfg.traversal == "bfs" else queue.pop

        while queue and pages_visited < cfg.max_pages:
            if clicks_remaining <= 0:
                break
            if time.monotonic() >= deadline:
                log.warning(
                    "max-duration %.0fs reached before page %d/%d → stopping explore",
                    cfg.max_duration,
                    pages_visited + 1,
                    cfg.max_pages,
                )
                break
            current = pop_next()
            key = normalize_url(current)
            if key in visited:
                continue
            visited.add(key)
            pages_visited += 1
            page_label = f"page {pages_visited}/{cfg.max_pages}"
            visit = _Visit(url=current, ordinal=len(visits))
            visits.append(visit)

            # --- open the page -------------------------------------------------
            goto = GotoStep(url=current, wait="networkidle")
            result = await execute(goto, sess, step_index=visit.ordinal)
            if not result.ok:
                typer.secho(
                    f"  skipped {current}: {result.error}", fg=typer.colors.YELLOW, err=True
                )
                log.warning("%s · skipped: %s", page_label, result.error)
                continue
            if cfg.initial_wait > 0:
                await sess.wait(cfg.initial_wait)
            visit.title = await sess.title()
            log.info("%s · open %s (%s)", page_label, current, visit.title)

            # --- discover, then click until budget/deadline --------------------
            elements = await discover(sess, limit=_EXPLORE_DISCOVERY_POOL)
            visit.discovered = elements
            log.info(
                "%s · discovered %d elements, click budget: %d",
                page_label,
                len(elements),
                clicks_remaining,
            )

            consecutive_failures = 0
            for element in elements:
                if clicks_remaining <= 0:
                    break
                if time.monotonic() >= deadline:
                    log.warning("%s · deadline reached during clicks → stopping page", page_label)
                    break
                step = ClickStep(
                    selector=element.selector,
                    optional=True,
                    label=element.text[:60] or element.role,
                    timeout_ms=cfg.click_timeout_ms,
                )
                url_before = sess.page.url
                r = await execute(step, sess, step_index=visit.ordinal)
                if r.status == "ok":
                    clicks_remaining -= 1
                    visit.clicks_ok += 1
                    consecutive_failures = 0
                else:
                    visit.clicks_failed += 1
                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        log.warning(
                            "%s · %d consecutive click failures → stopping page early",
                            page_label,
                            consecutive_failures,
                        )
                        break
                url_after = sess.page.url
                if url_after != url_before:
                    transitions.append((url_before, url_after, visit.ordinal, element.selector))
                    if url_after not in node_order:
                        node_order.append(url_after)
                    if (
                        is_same_origin(url_after, cfg.url)
                        and normalize_url(url_after) not in visited
                    ):
                        queue.append(url_after)
                    if not is_same_origin(url_after, url_before):
                        log.info(
                            "%s · nav to cross-origin %s → not driving further",
                            page_label,
                            url_after,
                        )
                        break
                    # Same-origin nav: restore the page so the remaining
                    # discovered elements still land on it.
                    try:
                        await sess.page.go_back(
                            wait_until="domcontentloaded", timeout=_GOTO_BACK_TIMEOUT_MS
                        )
                    except Exception as e:
                        log.warning("%s · go_back failed (%s) → stopping page", page_label, e)
                        break
                    if sess.page.url != url_before:
                        log.warning(
                            "%s · go_back landed at %s (expected %s) → stopping page",
                            page_label,
                            sess.page.url,
                            url_before,
                        )
                        break
                await sess.wait(_INTER_CLICK_WAIT_S)

    return _finalise(cfg, visits, titles, node_order, transitions, started)


def _finalise(
    cfg: ExploreConfig,
    visits: list[_Visit],
    titles: dict[str, str],
    node_order: list[str],
    transitions: list[tuple[str, str, int, str]],
    started: float,
) -> dict[str, Any]:
    """Assemble the exploration payload from the visit bookkeeping.

    Node step ranges are page-granular (exploration keeps no per-step ledger):
    ``first_seen_step``/``last_seen_step`` carry the visit ordinal, and an
    edge's ``via_step`` names the visit during which the transition fired —
    "found while exploring the page at that index", which is what a consumer
    planning a tour needs.
    """
    # Ordinal of the visit whose normalised URL matches — else the node was
    # only ever seen as a nav destination (never visited): park it one past
    # the last real visit so it still sorts after everything explored.
    visit_ordinal: dict[str, int] = {}
    for url in node_order:
        visit_ordinal[url] = len(visits)
        for visit in visits:
            if normalize_url(visit.url) == normalize_url(url):
                visit_ordinal[url] = visit.ordinal
                break

    node_id: dict[str, str] = {url: f"n{i + 1}" for i, url in enumerate(node_order)}

    pages_payload: list[dict[str, Any]] = [
        {
            "node": node_id[visit.url],
            "url": visit.url,
            "title": visit.title,
            "clicks_ok": visit.clicks_ok,
            "clicks_failed": visit.clicks_failed,
            "discovered": [e.to_dict() for e in visit.discovered[: cfg.per_page_discoveries]],
        }
        for visit in visits
    ]

    graph = Graph(
        nodes=[
            PageNode(
                id=node_id[url],
                url=url,
                title=titles.get(url, ""),
                first_seen_step=visit_ordinal[url],
                last_seen_step=visit_ordinal[url],
            )
            for url in node_order
        ],
        edges=[
            Edge.model_validate(
                {
                    "from": node_id[frm],
                    "to": node_id[to],
                    "via_step": ordinal,
                    "selector": selector,
                    "transition_kind": "navigation",
                }
            )
            for frm, to, ordinal, selector in transitions
        ],
    )

    return {
        "schema_version": 1,
        "kind": "clickcast/explore",
        "clickcast_version": __version__,
        "url": cfg.url,
        "traversal": cfg.traversal,
        "wall_s": round(time.monotonic() - started, 1),
        "pages": pages_payload,
        "graph": graph.model_dump(by_alias=True),
    }
