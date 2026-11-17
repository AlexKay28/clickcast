"""Deterministic, budgeted quiescence: "has the page stopped changing?" (#265).

The only quiescence primitive before this module was per-element bounding-box
polling (:func:`clickcast.core.actions._wait_for_stable`); global "settle" was
a pile of bare sleeps and ``networkidle``, which never fires on HMR /
WebSocket sites (see the comment beside ``_INTER_CLICK_WAIT_S`` in
:mod:`clickcast.auto`). This module answers the global question with three
independent, individually budgeted signals:

=========  ==================================================  =========  ========
Signal     Mechanism                                            Quiet       Budget
=========  ==================================================  =========  ========
``network`` own in-flight counter via page request events (#265)  500 ms     3000 ms
``dom``     idempotent ``MutationObserver`` in ``window.__ccMut`` 250 ms     2000 ms
``raf``     ``requestAnimationFrame`` counter + ``getAnimations``  250 ms     2000 ms
=========  ==================================================  =========  ========

Design rules:

- **Timeouts are data, never exceptions.** A signal that exhausts its budget
  is reported in ``timed_out``; :func:`settle` itself always returns.
- **No bare sleeps.** Every wait is a poll step capped by the signal deadline
  (``_pause``); ``tests/test_perception_settle.py`` AST-checks this.
- **Idempotent instrumentation.** Both observers live on the page
  (``window.__ccMut`` / ``window.__ccRaf``) and are reused across calls —
  installing twice neither fails nor leaks a second observer. A navigation
  naturally discards them with the old document; the poll loops reinstall.
- **Graceful degradation.** Old engines without ``document.getAnimations``
  lose only the CSS-animation half of the ``raf`` signal; ``ok`` can still be
  true.
- **Never raise on page churn.** Every page evaluation is bounded and
  exception-swallowed: a navigation, a destroyed execution context, or a
  closed page degrades into a reported timeout, not a traceback.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

from clickcast.core.session import Session

__all__ = [
    "DEFAULT_BUDGET_MS",
    "POLL_MS",
    "SIGNALS",
    "SettleResult",
    "SignalResult",
    "SignalSpec",
    "settle",
]

#: Network: 500 ms without a request starting or finishing.
NETWORK_QUIET_MS = 500
NETWORK_BUDGET_MS = 3000

#: DOM: 250 ms without a mutation record.
DOM_QUIET_MS = 250
DOM_BUDGET_MS = 2000

#: Compositor: 250 ms without a page-issued ``requestAnimationFrame`` and with
#: no running Web Animations.
RAF_QUIET_MS = 250
RAF_BUDGET_MS = 2000

#: Default *overall* budget — the largest per-signal budget, so every signal
#: can reach its own quiet window when the page cooperates.
DEFAULT_BUDGET_MS = max(NETWORK_BUDGET_MS, DOM_BUDGET_MS, RAF_BUDGET_MS)

#: Poll cadence for every signal. Polling (not a fixed sleep) is what keeps
#: early settles early and keeps the total under budget.
POLL_MS = 50

#: Cap on a single page evaluation. A navigation mid-poll can make
#: ``page.evaluate`` block on an execution context that no longer exists;
#: capping keeps the loop responsive and the total bounded by the budget.
_EVAL_CAP_S = 0.25


@dataclass(frozen=True, slots=True)
class SignalSpec:
    """Static description of one quiescence signal."""

    name: str
    quiet_ms: int
    budget_ms: int


#: The three signals, in report order. The table in the module docstring and
#: the PR body must stay in sync with these numbers.
SIGNALS: tuple[SignalSpec, ...] = (
    SignalSpec(name="network", quiet_ms=NETWORK_QUIET_MS, budget_ms=NETWORK_BUDGET_MS),
    SignalSpec(name="dom", quiet_ms=DOM_QUIET_MS, budget_ms=DOM_BUDGET_MS),
    SignalSpec(name="raf", quiet_ms=RAF_QUIET_MS, budget_ms=RAF_BUDGET_MS),
)


@dataclass(frozen=True, slots=True)
class SignalResult:
    """Outcome of one signal's poll loop."""

    name: str
    ok: bool
    elapsed_ms: int
    budget_ms: int
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "ms": self.elapsed_ms,
            "budget_ms": self.budget_ms,
        }
        payload.update(self.detail)
        return payload


@dataclass(frozen=True, slots=True)
class SettleResult:
    """Result of :func:`settle` — JSON shape is part of the public contract.

    ``ok`` is true iff every signal reached its quiet window. ``timed_out``
    names the signals that did not (empty on success). A timed-out settle is
    still a *successful observation*: the caller learns the page is busy
    instead of being lied to by a bare sleep.
    """

    ms: int
    signals: dict[str, dict[str, Any]]
    timed_out: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ms": self.ms,
            "signals": self.signals,
            "timed_out": list(self.timed_out),
        }


# ---------------------------------------------------------------------------
# Page-side instrumentation (installed once, reused forever)
# ---------------------------------------------------------------------------

# Idempotent: a second call (or a second settle) sees the existing observer and
# returns immediately, so no observer is ever leaked.
_INSTALL_MUT = """
() => {
  if (window.__ccMut && window.__ccMut.observer) {
    return true;
  }
  const state = {
    count: 0,
    last: performance.now(),
    installed: performance.now(),
    observer: null,
  };
  state.observer = new MutationObserver((records) => {
    state.count += records.length;
    state.last = performance.now();
  });
  const root = document.documentElement;
  if (!root) {
    return false;
  }
  state.observer.observe(root, {
    subtree: true,
    childList: true,
    attributes: true,
    characterData: true,
  });
  window.__ccMut = state;
  return true;
}
"""

_READ_MUT = """
() => {
  const state = window.__ccMut;
  if (!state || !state.observer) {
    return { installed: false };
  }
  return {
    installed: true,
    quiet_ms: performance.now() - Math.max(state.installed, state.last),
    mutations: state.count,
  };
}
"""

# Wrap requestAnimationFrame so page-issued frame requests are observable
# without our own rAF loop (which would otherwise tick forever and look like
# activity). Callers keep the native cancellation ids, and window-level
# lookups inside page callbacks pick the wrapper up on the next frame.
_INSTALL_RAF = """
() => {
  if (window.__ccRaf && window.__ccRaf.wrapped) {
    return true;
  }
  const native = window.requestAnimationFrame;
  if (typeof native !== 'function') {
    return false;
  }
  const state = { last: performance.now(), count: 0, native: native };
  state.wrapped = function (callback) {
    state.count += 1;
    state.last = performance.now();
    return state.native.call(window, callback);
  };
  window.requestAnimationFrame = state.wrapped;
  window.__ccRaf = state;
  return true;
}
"""

_READ_RAF = """
() => {
  let supported = typeof document.getAnimations === 'function';
  let animations = 0;
  if (supported) {
    try {
      animations = document.getAnimations().length;
    } catch (err) {
      supported = false;
      animations = 0;
    }
  }
  const state = window.__ccRaf;
  if (!state || !state.wrapped) {
    return { installed: false, supported: supported, animations: animations };
  }
  return {
    installed: true,
    supported: supported,
    animations: animations,
    quiet_ms: performance.now() - state.last,
    frames: state.count,
  };
}
"""


# ---------------------------------------------------------------------------
# Network accounting
# ---------------------------------------------------------------------------


class _NetworkCounter:
    """Own in-flight request counter fed by page request events.

    Deliberately not ``networkidle``: HMR sockets and long polls never satisfy
    it (see :mod:`clickcast.auto`). Events are page-level, so they survive
    same-page navigations; a cross-document navigation simply keeps feeding
    the same counter.

    One counter lives for the lifetime of a :class:`Session` (see
    :func:`_counter_for`). That is what makes requests *already in flight*
    when :func:`settle` starts visible: a counter attached at call time would
    miss their ``request`` events, which fired earlier.
    """

    def __init__(self) -> None:
        self._pending: set[int] = set()
        self._total = 0
        self._last_change = time.monotonic()

    @property
    def inflight(self) -> int:
        return len(self._pending)

    @property
    def total(self) -> int:
        return self._total

    def quiet_for(self, now: float) -> float:
        """Seconds since the last request event, or ``0`` while requests are in flight."""
        if self._pending:
            return 0.0
        return max(0.0, now - self._last_change)

    def _mark(self) -> None:
        self._last_change = time.monotonic()

    def _on_request(self, request: Any) -> None:
        self._pending.add(id(request))
        self._total += 1
        self._mark()

    def _on_finished(self, request: Any) -> None:
        self._pending.discard(id(request))
        self._mark()

    def attach(self, session: Session) -> None:
        self._last_change = time.monotonic()
        session.on("request", self._on_request)
        session.on("requestfinished", self._on_finished)
        session.on("requestfailed", self._on_finished)


#: One counter per live session, attached on first use and reused afterwards.
#: ``WeakKeyDictionary`` so a closed/GC'd session doesn't keep the process
#: (or the page's event listeners) alive.
_COUNTERS: weakref.WeakKeyDictionary[Session, _NetworkCounter] = weakref.WeakKeyDictionary()


def _counter_for(session: Session) -> _NetworkCounter:
    counter = _COUNTERS.get(session)
    if counter is None:
        counter = _NetworkCounter()
        counter.attach(session)
        _COUNTERS[session] = counter
    return counter


# ---------------------------------------------------------------------------
# Budgeted poll helpers
# ---------------------------------------------------------------------------


async def _pause(deadline: float, poll_s: float = POLL_MS / 1000.0) -> None:
    """Sleep one poll step, never past ``deadline``.

    This is the only ``asyncio.sleep`` in the package. The AST test in
    ``tests/test_perception_settle.py`` requires its argument to be a
    ``min(...)`` of the poll cadence and the remaining time to the deadline —
    no unbudgeted pacing anywhere under ``perception/``.
    """
    loop = asyncio.get_running_loop()
    if deadline - loop.time() <= 0:
        return
    await asyncio.sleep(min(poll_s, deadline - loop.time()))


async def _safe_evaluate(session: Session, script: str, timeout: float) -> Any:
    """Run ``script``, bounded and exception-swallowed.

    A navigation or closed page mid-settle destroys the execution context;
    that must degrade into "no data this poll", never a raise.
    """
    if timeout <= 0:
        return None
    try:
        return await asyncio.wait_for(session.evaluate(script), timeout=timeout)
    except Exception:
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000.0))


def _signal_deadline(deadline: float, budget_ms: int) -> float:
    return min(deadline, time.monotonic() + budget_ms / 1000.0)


# ---------------------------------------------------------------------------
# Signal poll loops
# ---------------------------------------------------------------------------


async def _poll_network(
    counter: _NetworkCounter, *, budget_ms: int, deadline: float
) -> SignalResult:
    started = time.monotonic()
    signal_deadline = _signal_deadline(deadline, budget_ms)
    quiet_s = NETWORK_QUIET_MS / 1000.0
    while True:
        now = time.monotonic()
        if counter.quiet_for(now) >= quiet_s:
            return SignalResult(
                name="network",
                ok=True,
                elapsed_ms=_elapsed_ms(started),
                budget_ms=budget_ms,
                detail={"requests": counter.total},
            )
        if now >= signal_deadline:
            return SignalResult(
                name="network",
                ok=False,
                elapsed_ms=_elapsed_ms(started),
                budget_ms=budget_ms,
                detail={"requests": counter.total, "inflight": counter.inflight},
            )
        await _pause(signal_deadline)


async def _poll_dom(session: Session, *, budget_ms: int, deadline: float) -> SignalResult:
    started = time.monotonic()
    signal_deadline = _signal_deadline(deadline, budget_ms)
    mutations = 0
    while True:
        now = time.monotonic()
        remaining = signal_deadline - now
        if remaining <= 0:
            return SignalResult(
                name="dom",
                ok=False,
                elapsed_ms=_elapsed_ms(started),
                budget_ms=budget_ms,
                detail={"mutations": mutations},
            )
        data = await _safe_evaluate(session, _READ_MUT, timeout=min(remaining, _EVAL_CAP_S))
        if isinstance(data, dict) and data.get("installed"):
            mutations = _as_int(data.get("mutations"))
            if _as_float(data.get("quiet_ms")) >= DOM_QUIET_MS:
                return SignalResult(
                    name="dom",
                    ok=True,
                    elapsed_ms=_elapsed_ms(started),
                    budget_ms=budget_ms,
                    detail={"mutations": mutations},
                )
        else:
            # Missing observer: first poll, or a navigation replaced the
            # document. Reinstall; never stack a second observer.
            await _safe_evaluate(session, _INSTALL_MUT, timeout=min(remaining, _EVAL_CAP_S))
        await _pause(signal_deadline)


async def _poll_raf(session: Session, *, budget_ms: int, deadline: float) -> SignalResult:
    started = time.monotonic()
    signal_deadline = _signal_deadline(deadline, budget_ms)
    animations = 0
    supported = True
    frames = 0
    while True:
        now = time.monotonic()
        remaining = signal_deadline - now
        if remaining <= 0:
            return SignalResult(
                name="raf",
                ok=False,
                elapsed_ms=_elapsed_ms(started),
                budget_ms=budget_ms,
                detail={"animations": animations, "supported": supported, "frames": frames},
            )
        data = await _safe_evaluate(session, _READ_RAF, timeout=min(remaining, _EVAL_CAP_S))
        if isinstance(data, dict) and data.get("installed"):
            animations = _as_int(data.get("animations"))
            supported = bool(data.get("supported", True))
            frames = _as_int(data.get("frames"))
            # Without getAnimations we lose only the CSS half of this signal;
            # the rAF counter still guards frame-driven animation.
            if _as_float(data.get("quiet_ms")) >= RAF_QUIET_MS and animations == 0:
                return SignalResult(
                    name="raf",
                    ok=True,
                    elapsed_ms=_elapsed_ms(started),
                    budget_ms=budget_ms,
                    detail={"animations": animations, "supported": supported, "frames": frames},
                )
        else:
            await _safe_evaluate(session, _INSTALL_RAF, timeout=min(remaining, _EVAL_CAP_S))
        await _pause(signal_deadline)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def settle(session: Session, *, budget_ms: int | None = None) -> SettleResult:
    """Wait until ``session``'s page is quiet, within ``budget_ms``.

    Runs the three signals concurrently. Each gets its own budget (see
    :data:`SIGNALS`), additionally clamped to the overall deadline, so a
    caller-provided ``budget_ms`` is a hard ceiling on the whole call. Nothing
    is raised on timeout — the exhausted signals are named in
    :attr:`SettleResult.timed_out`.

    ``budget_ms=None`` uses :data:`DEFAULT_BUDGET_MS` (3000 ms).
    """
    total_ms = DEFAULT_BUDGET_MS if budget_ms is None else max(1, int(budget_ms))
    started = time.monotonic()
    deadline = started + total_ms / 1000.0

    counter = _counter_for(session)
    network, dom, raf = await asyncio.gather(
        _poll_network(counter, budget_ms=min(NETWORK_BUDGET_MS, total_ms), deadline=deadline),
        _poll_dom(session, budget_ms=min(DOM_BUDGET_MS, total_ms), deadline=deadline),
        _poll_raf(session, budget_ms=min(RAF_BUDGET_MS, total_ms), deadline=deadline),
    )

    results = (network, dom, raf)
    return SettleResult(
        ms=_elapsed_ms(started),
        signals={result.name: result.to_dict() for result in results},
        timed_out=tuple(result.name for result in results if not result.ok),
    )
