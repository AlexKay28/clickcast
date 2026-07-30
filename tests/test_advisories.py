"""Unit tests for :mod:`clickcast.feedback.advisories` — Track A of #138.

One test per shipped advisory id, plus empty-tour and well-formed-tour
"nothing to flag" tests. Hand-built fixtures throughout — no Playwright, no
recorder, no file I/O.
"""

from __future__ import annotations

from clickcast.feedback import Advisory, Media, PageState, StepReport, build_advisories


def _media(*, frame_count: int = 60) -> Media:
    return Media(
        path="tour.gif",
        format="gif",
        size_bytes=1024,
        frame_count=frame_count,
        duration_s=7.4,
        fps=8,
    )


def _click(
    index: int,
    *,
    url: str,
    title: str = "T",
    label: str | None = None,
    status: str = "ok",
) -> StepReport:
    return StepReport(
        index=index,
        action="click",
        args={"selector": f"#sel-{index}"},
        status=status,
        duration_ms=100.0,
        label=label,
        page_state=PageState(url_after=url, title=title),
    )


def _goto(index: int, *, url: str, title: str = "T") -> StepReport:
    return StepReport(
        index=index,
        action="goto",
        args={"url": url},
        status="ok",
        duration_ms=200.0,
        page_state=PageState(url_after=url, title=title),
    )


def _ids(advs: list[Advisory]) -> list[str]:
    return [a.id for a in advs]


class TestEmptyTour:
    def test_no_steps_no_clicks_produces_no_advisories(self) -> None:
        advs = build_advisories([], _media(), total_clicks=0, nav_clicks=0)
        assert advs == []


class TestWellFormedTour:
    def test_in_place_tour_produces_no_advisories(self) -> None:
        # Same-origin, dropdown-style clicks that toggle title on each click,
        # 60 frames — matches the tailwindcss.com shipped demo shape.
        steps = [
            _goto(0, url="https://example.com/", title="Home"),
            _click(1, url="https://example.com/", title="Home - menu open"),
            _click(2, url="https://example.com/", title="Home"),
            _click(3, url="https://example.com/", title="Home - search open"),
            _click(4, url="https://example.com/", title="Home"),
        ]
        advs = build_advisories(steps, _media(frame_count=60), total_clicks=4, nav_clicks=0)
        assert advs == []


class TestNavHeavyTour:
    def test_more_than_half_of_clicks_navigating_triggers(self) -> None:
        advs = build_advisories([], _media(), total_clicks=5, nav_clicks=4)
        assert "nav-heavy-tour" in _ids(advs)
        nav = next(a for a in advs if a.id == "nav-heavy-tour")
        assert "4 of 5" in nav.message
        assert "ONE_PAGE_NAVIGATION_ORDER_TIPS" in nav.doc_url

    def test_exactly_half_does_not_trigger(self) -> None:
        # Threshold is strictly greater-than 0.5; equal-to should NOT warn.
        advs = build_advisories([], _media(), total_clicks=4, nav_clicks=2)
        assert "nav-heavy-tour" not in _ids(advs)

    def test_zero_clicks_does_not_divide_by_zero(self) -> None:
        advs = build_advisories([], _media(), total_clicks=0, nav_clicks=0)
        assert "nav-heavy-tour" not in _ids(advs)


class TestClickNoDomReaction:
    def test_click_with_no_url_or_title_change_triggers(self) -> None:
        steps = [
            _goto(0, url="https://example.com/", title="Home"),
            _click(1, url="https://example.com/", title="Home", label="Dead button"),
        ]
        advs = build_advisories(steps, _media(), total_clicks=1, nav_clicks=0)
        assert "click-no-dom-reaction" in _ids(advs)
        msg = next(a for a in advs if a.id == "click-no-dom-reaction").message
        assert "Dead button" in msg
        assert "Step 1" in msg

    def test_click_that_changes_title_does_not_trigger(self) -> None:
        steps = [
            _goto(0, url="https://example.com/", title="Home"),
            _click(1, url="https://example.com/", title="Home - menu open"),
        ]
        advs = build_advisories(steps, _media(), total_clicks=1, nav_clicks=0)
        assert "click-no-dom-reaction" not in _ids(advs)

    def test_failed_click_is_not_flagged(self) -> None:
        # Failed clicks legitimately don't change the DOM — not an anti-pattern.
        steps = [
            _goto(0, url="https://example.com/", title="Home"),
            _click(1, url="https://example.com/", title="Home", status="failed"),
        ]
        advs = build_advisories(steps, _media(), total_clicks=0, nav_clicks=0)
        assert "click-no-dom-reaction" not in _ids(advs)


class TestVeryShortReel:
    def test_below_20_frames_triggers(self) -> None:
        advs = build_advisories([], _media(frame_count=15), total_clicks=0, nav_clicks=0)
        assert "very-short-reel" in _ids(advs)
        msg = next(a for a in advs if a.id == "very-short-reel").message
        assert "15 frames" in msg

    def test_exactly_20_frames_does_not_trigger(self) -> None:
        advs = build_advisories([], _media(frame_count=20), total_clicks=0, nav_clicks=0)
        assert "very-short-reel" not in _ids(advs)


class TestCrossOriginBounce:
    def test_cross_origin_click_then_return_triggers(self) -> None:
        steps = [
            _goto(0, url="https://example.com/", title="Home"),
            _click(1, url="https://external.com/page", title="External"),
            # ``auto`` bailed cross-origin and called ``go_back`` — the next
            # step lands back at the previous origin.
            _click(2, url="https://example.com/", title="Home"),
        ]
        advs = build_advisories(steps, _media(), total_clicks=2, nav_clicks=2)
        assert "cross-origin-bounce" in _ids(advs)
        msg = next(a for a in advs if a.id == "cross-origin-bounce").message
        assert "external.com" in msg
        assert "Step 1" in msg

    def test_cross_origin_click_that_stays_cross_origin_does_not_trigger(self) -> None:
        # No bounce back — the tour ended on the external site. Different
        # anti-pattern, not this one.
        steps = [
            _goto(0, url="https://example.com/", title="Home"),
            _click(1, url="https://external.com/page", title="External"),
        ]
        advs = build_advisories(steps, _media(), total_clicks=1, nav_clicks=1)
        assert "cross-origin-bounce" not in _ids(advs)

    def test_same_origin_navigation_does_not_trigger(self) -> None:
        steps = [
            _goto(0, url="https://example.com/", title="Home"),
            _click(1, url="https://example.com/docs", title="Docs"),
            _click(2, url="https://example.com/", title="Home"),
        ]
        advs = build_advisories(steps, _media(), total_clicks=2, nav_clicks=2)
        assert "cross-origin-bounce" not in _ids(advs)


class TestAdvisoryShape:
    def test_advisory_is_frozen(self) -> None:
        import dataclasses

        adv = Advisory(id="x", message="m", doc_url="u")
        assert dataclasses.is_dataclass(adv)
        try:
            adv.id = "y"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("Advisory should be frozen")
