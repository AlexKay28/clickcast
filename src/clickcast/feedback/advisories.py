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
- ``click-no-dom-reaction`` — a click's ``page_state`` shows no observable
  change (title / url identical to the pre-click state).
- ``very-short-reel`` — encoded reel is < 20 frames.
- ``cross-origin-bounce`` — a step navigated cross-origin and the tour
  returned to the previous origin (an ``auto`` ``go_back``).

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
from urllib.parse import urlparse

from clickcast.feedback.models import Media, StepReport

__all__ = ["Advisory", "build_advisories"]


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
) -> list[Advisory]:
    """Score a completed tour against known anti-patterns.

    ``steps`` is the same list :class:`clickcast.feedback.ReportBuilder` accumulates
    during a run. ``media`` is the encoded reel's metadata. ``total_clicks``
    and ``nav_clicks`` are tour-level totals the orchestrator already tracks
    (passing them in rather than re-deriving keeps this function honest about
    what it consumes and lets the orchestrator use the same counts it prints
    in the summary line).

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


def _check_click_no_dom_reaction(steps: list[StepReport]) -> list[Advisory]:
    """Flag click steps whose ``page_state`` shows no observable change.

    Conservative heuristic: we only have ``PageState.url_after`` and
    ``PageState.title`` to work with today. If BOTH are identical to the
    previous step's snapshot AND the click was recorded as ``ok`` (so it
    actually landed on the target), emit the advisory. The click may have
    been a no-op — the viewer sees the cursor ripple but nothing else.

    Skips clicks that caused a same-page overlay to render (``title``
    change) or navigation (``url_after`` change) — both are visible
    reactions. See the module docstring for the richer-signal follow-up.
    """
    out: list[Advisory] = []
    prev_state = None
    for step in steps:
        current = step.page_state
        if step.action != "click" or step.status != "ok":
            prev_state = current
            continue
        if current is None or prev_state is None:
            prev_state = current
            continue
        same_url = current.url_after == prev_state.url_after
        same_title = current.title == prev_state.title
        if same_url and same_title:
            label = step.label or step.args.get("selector", "step")
            out.append(
                Advisory(
                    id="click-no-dom-reaction",
                    message=(
                        f"Step {step.index} clicked '{label}' but the page URL "
                        "and title didn't change. The click may have been a no-op."
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
    for i, step in enumerate(steps):
        current = step.page_state
        if current is None or not current.url_after:
            continue
        prev = _prev_url_state(steps, i)
        if prev is None or not prev:
            continue
        if _same_origin(current.url_after, prev):
            continue
        # We navigated cross-origin. Did a later step bounce us back?
        returned = _next_url_state(steps, i)
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


def _prev_url_state(steps: list[StepReport], i: int) -> str | None:
    for j in range(i - 1, -1, -1):
        state = steps[j].page_state
        if state is not None and state.url_after:
            return state.url_after
    return None


def _next_url_state(steps: list[StepReport], i: int) -> str | None:
    for j in range(i + 1, len(steps)):
        state = steps[j].page_state
        if state is not None and state.url_after:
            return state.url_after
    return None


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)
