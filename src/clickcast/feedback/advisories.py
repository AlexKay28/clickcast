"""Post-tour heuristic advisories — Track A of #138.

The problem this solves: an AI agent invokes ``clickcast auto`` on a nav-heavy
docs site, gets a scene-cut-riddled reel, and ships it — because nothing in
the tool's runtime output flagged the mistake. Docs help (once found and
read); ``clickcast skill`` surfaces the capability list; the sidecar carries
structural facts. But nothing fires *after the run completes* to say "this
tour matches a known anti-pattern".

This module is that missing signal. :func:`build_advisories` takes the
completed tour data — the ``StepReport`` sequence, the encoded ``Media``, and
a couple of tour-level totals — and returns a list of :class:`Advisory`
records. The orchestrator in :mod:`clickcast.auto` prints each one to stderr
with a ``⚠ `` marker at the tail of a run, mirroring the shipped
``report-bug`` style. Every advisory carries a stable ``id`` so agents
parsing stderr can match / dedupe / gate, and a ``doc_url`` deep-linking
to the guide section that explains the fix.

Design constraints:

- **Pure function.** No I/O, no Playwright, no side effects. Callable with
  hand-built fixtures from tests.
- **Conservative.** When a signal is ambiguous, prefer under-warning to
  false-positive — a spurious warning trains the agent to ignore all of them.
- **Additive.** New ids append; existing ids never change semantics. Downstream
  gates keyed on the id remain stable.

Shipped advisory ids (see individual builder functions below for the exact
trigger rules):

- ``nav-heavy-tour`` — >50% of clicks caused a page navigation.
- ``click-no-dom-reaction`` — a click produced no observable change. Uses the
  per-step structured delta when the writer captured one (#264); falls back to
  the original ``page_state`` url/title comparison otherwise.
- ``very-short-reel`` — encoded reel is < 20 frames.
- ``cross-origin-bounce`` — a step navigated cross-origin and the tour
  returned to the previous origin (an ``auto`` ``go_back``).
- ``interpolate-single-arrow-conflict`` — ``CursorStyle.interpolate=True``
  and ``single_arrow=True`` are both set; the sticky arrow smears through
  the interpolated cursor path (visually incoherent).
- ``arrow-distance-vs-cross-nav`` — a tour with ≥1 cross-origin edge and
  ``CursorStyle.arrow_max_distance`` at its shipped default silently
  suppresses the closing arrow of the previous scene.

Explicit follow-ups (out of scope for this PR, tracked on #138):

- Richer ``click-no-dom-reaction``: today it only compares URL + title
  because that's what ``PageState`` carries. When we start recording an
  interactive-element-count or DOM-signature delta per step, this heuristic
  should upgrade to use it.
- Tracks B-G from #138 (sidecar ``quality`` block, ``clickcast lint``,
  skill ``pitfalls``, inline error hints, post-run summary integration,
  ``doctor --for-agent``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from clickcast.feedback.models import Media, PageState, StepDelta, StepReport

if TYPE_CHECKING:
    from clickcast.annotate import AnnotateConfig

__all__ = ["Advisory", "build_advisories", "no_dom_reaction"]


# Deep link every advisory to the guide section that names the fix. An AI
# reading stderr can follow this pointer programmatically; a human clicks it.
_TIPS_URL = (
    "https://github.com/AlexKay28/clickcast/blob/main/docs/ONE_PAGE_NAVIGATION_ORDER_TIPS.md"
)

# The nav-heavy threshold: >50% of clicks turned into navigations. Below this
# a tour is "mostly in-place with occasional navigation" — legitimate. Above,
# every other click is a scene cut. See TIPS §1 ("Stay on one page").
_NAV_HEAVY_RATIO = 0.5

# A reel shorter than this many frames doesn't give the eye time to catch up.
# Matches the "5-8 steps x ~1.2s dwell x 8 fps ~= 50-80 frames" heuristic from
# TIPS section 6; 20 frames is the floor below which "advisory" is uncontroversial.
_SHORT_REEL_FRAME_FLOOR = 20

# The shipped default for :attr:`CursorStyle.arrow_max_distance`. Hard-coded
# here (rather than imported from ``clickcast.annotate``) so this module stays
# import-free of the annotate package — advisories are pure heuristic code,
# usable from tests that don't need Pillow/font machinery on the path. Kept in
# sync by the dedicated regression test in ``tests/test_advisories.py``.
_DEFAULT_ARROW_MAX_DISTANCE = 600


@dataclass(frozen=True, slots=True)
class Advisory:
    """One heuristic finding about a completed tour.

    ``id`` is a stable machine-matchable key (kebab-case, never renamed once
    shipped). ``message`` is the one-line stderr rendering. ``doc_url`` is the
    URL an AI agent (or human) can follow to read the fix.
    """

    id: str
    message: str
    doc_url: str


def build_advisories(
    steps: list[StepReport],
    media: Media,
    *,
    total_clicks: int,
    nav_clicks: int,
    annotate_cfg: AnnotateConfig | None = None,
) -> list[Advisory]:
    """Score a completed tour against known anti-patterns.

    ``steps`` is the same list :class:`clickcast.feedback.ReportBuilder` accumulates
    during a run. ``media`` is the encoded reel's metadata. ``total_clicks``
    and ``nav_clicks`` are tour-level totals the orchestrator already tracks
    (passing them in rather than re-deriving keeps this function honest about
    what it consumes and lets the orchestrator use the same counts it prints
    in the summary line). ``annotate_cfg`` — when supplied — enables the
    cursor-style anti-pattern checks (``interpolate-single-arrow-conflict``
    and ``arrow-distance-vs-cross-nav``). Optional so hand-fixtured tests
    that don't care about the annotate layer keep working unchanged.

    Returns a list of :class:`Advisory` — empty for a well-formed tour.
    Order matches the order the checks run, which is roughly "biggest
    signal first" so an agent that only reads the first advisory still
    gets the most actionable one.
    """
    advisories: list[Advisory] = []
    if nav_heavy := _check_nav_heavy(total_clicks=total_clicks, nav_clicks=nav_clicks):
        advisories.append(nav_heavy)
    if short := _check_short_reel(media):
        advisories.append(short)
    advisories.extend(_check_click_no_dom_reaction(steps))
    advisories.extend(_check_cross_origin_bounce(steps))
    if annotate_cfg is not None:
        if conflict := _check_interpolate_single_arrow_conflict(annotate_cfg):
            advisories.append(conflict)
        if arrow_dist := _check_arrow_distance_vs_cross_nav(steps, annotate_cfg):
            advisories.append(arrow_dist)
    return advisories


def _check_nav_heavy(*, total_clicks: int, nav_clicks: int) -> Advisory | None:
    if total_clicks <= 0:
        return None
    if nav_clicks / total_clicks <= _NAV_HEAVY_RATIO:
        return None
    return Advisory(
        id="nav-heavy-tour",
        message=(
            f"{nav_clicks} of {total_clicks} clicks caused page navigations. "
            "`auto` on nav-heavy sites produces scene cuts. Consider a scripted "
            "scenario (`clickcast run`)."
        ),
        doc_url=_TIPS_URL,
    )


def _check_short_reel(media: Media) -> Advisory | None:
    if media.frame_count >= _SHORT_REEL_FRAME_FLOOR:
        return None
    return Advisory(
        id="very-short-reel",
        message=(
            f"Reel is only {media.frame_count} frames "
            f"(< {_SHORT_REEL_FRAME_FLOOR}). Add more steps or increase `--dwell`."
        ),
        doc_url=_TIPS_URL,
    )


def no_dom_reaction(
    prev: PageState | None,
    current: PageState | None,
    delta: StepDelta | None = None,
) -> bool:
    """True when ``current`` shows no observable change from ``prev``.

    When a structured :class:`~clickcast.feedback.models.StepDelta` is
    available (#264) it is authoritative: it already compares the page
    before and after *this* action, so a same-URL SPA reaction (a revealed
    panel, a new row, changed text) is no longer a false positive — the
    exact class of bug tracked in #228. The delta path also trusts the
    step's own console/network errors and a genuine layout shift as
    reaction evidence.

    With no delta (batch runs, older transcripts, failed steps) the
    original conservative url/title comparison still applies: ``None`` on
    either side means "no baseline to compare", which is treated as NOT
    flaggable.

    Shared between :func:`_check_click_no_dom_reaction` (the tour-level
    stderr advisory, every click) and
    :meth:`clickcast.feedback.builder.ReportBuilder.record_step` (the
    per-step ``skip_reason="optional_no_reaction"`` gate, optional clicks
    only) so the two never drift on what counts as "no reaction".
    """
    if delta is not None:
        return not _delta_shows_reaction(prev, delta)

    if prev is None or current is None:
        return False
    # A console/page error or failed request recorded *for this step* is
    # direct evidence something happened even when url/title didn't change —
    # cheap, zero-schema-change signal using data already collected per-step.
    if current.console_errors or current.page_errors or current.network_failed:
        return False
    return current.url_after == prev.url_after and current.title == prev.title


def _delta_shows_reaction(prev: PageState | None, delta: StepDelta) -> bool:
    """True when ``delta`` carries evidence the action did something."""
    if delta.console_errors or delta.network_failed:
        return True
    if prev is not None:
        # ``delta.url`` / ``delta.title`` are the post-action values; compare
        # against the previous step's state when both sides are known.
        if delta.url and prev.url_after and delta.url != prev.url_after:
            return True
        if delta.title and prev.title and delta.title != prev.title:
            return True
    return bool(delta.added or delta.removed or delta.text_changes or delta.layout_shift)


def _check_click_no_dom_reaction(steps: list[StepReport]) -> list[Advisory]:
    """Flag click steps whose ``page_state`` shows no observable change.

    Skips clicks that caused a same-page overlay to render (``title``
    change) or navigation (``url_after`` change) — both are visible
    reactions. Also skips any step that :class:`ReportBuilder` already
    downgraded to ``status="skipped"`` / ``skip_reason="optional_no_reaction"``
    (#227) — that's the same signal, just carried on the step itself
    instead of as a tour-level warning, so flagging it here too would be
    redundant.
    """
    out: list[Advisory] = []
    prev_state = None
    for step in steps:
        current = step.page_state
        if (
            step.action == "click"
            and step.status == "ok"
            and no_dom_reaction(prev_state, current, step.delta)
        ):
            label = step.label or step.args.get("selector", "step")
            out.append(
                Advisory(
                    id="click-no-dom-reaction",
                    message=(
                        f"Step {step.index} clicked '{label}' but nothing on the "
                        "page changed. The click may have been a no-op."
                    ),
                    doc_url=_TIPS_URL,
                )
            )
        prev_state = current
    return out


def _check_cross_origin_bounce(steps: list[StepReport]) -> list[Advisory]:
    """Flag steps that navigated cross-origin and were then returned via ``go_back``.

    The signature we look for: step N's ``page_state.url_after`` has a different
    origin than step N-1's, and step N+k (nearest subsequent step with a
    ``page_state``) landed back at step N-1's origin. That's the ``auto``
    ``go_back`` recovery — a scene cut with a jump-cut return, exactly the
    "reads as buggy" case Anti-pattern 2 in TIPS calls out.
    """
    out: list[Advisory] = []
    previous_urls, next_urls = _adjacent_url_states(steps)
    for i, step in enumerate(steps):
        current = step.page_state
        if current is None or not current.url_after:
            continue
        prev = previous_urls[i]
        if prev is None:
            continue
        if _same_origin(current.url_after, prev):
            continue
        # We navigated cross-origin. Did a later step bounce us back?
        returned = next_urls[i]
        if returned is None or not returned:
            continue
        if not _same_origin(returned, prev):
            continue
        out.append(
            Advisory(
                id="cross-origin-bounce",
                message=(
                    f"Step {step.index} navigated cross-origin to "
                    f"{urlparse(current.url_after).netloc or current.url_after}; "
                    "auto returned to the previous origin. This produces a "
                    "jump-cut in the reel."
                ),
                doc_url=_TIPS_URL,
            )
        )
    return out


def _adjacent_url_states(
    steps: list[StepReport],
) -> tuple[list[str | None], list[str | None]]:
    """Return nearest preceding/following URL for each step in two linear passes."""
    urls = [
        step.page_state.url_after
        if step.page_state is not None and step.page_state.url_after
        else None
        for step in steps
    ]
    previous: list[str | None] = []
    prev_url: str | None = None
    for url in urls:
        previous.append(prev_url)
        if url is not None:
            prev_url = url

    following: list[str | None] = [None] * len(steps)
    next_url: str | None = None
    for i in range(len(steps) - 1, -1, -1):
        following[i] = next_url
        if urls[i] is not None:
            next_url = urls[i]
    return previous, following


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)


def _check_interpolate_single_arrow_conflict(cfg: AnnotateConfig) -> Advisory | None:
    """Flag ``interpolate=True`` + ``single_arrow=True`` on the cursor style.

    Interpolation inserts intermediate cursor frames along the path between
    two recorded positions; ``single_arrow`` holds one A→B arrow across the
    step's dwell. Combined, the sticky arrow's endpoints track along the
    interpolated path — the sticky arrow "smears" as it walks. Anti-pattern
    4 in :file:`docs/ONE_PAGE_NAVIGATION_ORDER_TIPS.md`.
    """
    style = cfg.cursor_style
    if not (style.interpolate and style.single_arrow):
        return None
    return Advisory(
        id="interpolate-single-arrow-conflict",
        message=(
            "cursor interpolation + single_arrow both enabled — the sticky "
            "arrow smears through the interpolated cursor path (visually "
            "incoherent). Pick one."
        ),
        doc_url=_TIPS_URL,
    )


def _check_arrow_distance_vs_cross_nav(
    steps: list[StepReport], cfg: AnnotateConfig
) -> Advisory | None:
    """Flag default ``arrow_max_distance`` on a tour with cross-origin edges.

    The shipped default (600 px) filters "teleports" — cursor jumps between
    unrelated positions across a page navigation, which read as misleading
    long arrows. On a tour that DOES cross origins, though, the closing
    arrow of the previous scene is exactly what threads the two scenes
    together visually — suppressing it cuts the tour's through-line.
    Anti-pattern 5 in :file:`docs/ONE_PAGE_NAVIGATION_ORDER_TIPS.md`.
    """
    if cfg.cursor_style.arrow_max_distance != _DEFAULT_ARROW_MAX_DISTANCE:
        return None
    if not _has_cross_origin_edge(steps):
        return None
    return Advisory(
        id="arrow-distance-vs-cross-nav",
        message=(
            "cross-origin edges + default arrow_max_distance hide the closing "
            "arrow of the previous scene, cutting the visual thread."
        ),
        doc_url=_TIPS_URL,
    )


def _has_cross_origin_edge(steps: list[StepReport]) -> bool:
    """True iff any consecutive pair of URL-carrying steps crosses origins.

    Mirrors the walk in :func:`_check_cross_origin_bounce` but only cares
    about the first cross-origin edge — no return-bounce requirement.
    """
    prev_url: str | None = None
    for step in steps:
        state = step.page_state
        if state is None or not state.url_after:
            continue
        if prev_url is not None and not _same_origin(state.url_after, prev_url):
            return True
        prev_url = state.url_after
    return False
