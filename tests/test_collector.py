"""Unit tests for PageStateCollector — including the detach lifecycle."""

from __future__ import annotations

from typing import Any

from clickcast.feedback.collector import PageStateCollector


class _FakeConsoleMsg:
    def __init__(self, msg_type: str, text: str) -> None:
        self.type = msg_type
        self.text = text


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakePage:
    """Enough of a Playwright Page for the collector to run against."""

    def __init__(self, title: str = "T", url: str = "http://x/") -> None:
        self._title = title
        self.url = url
        self._listeners: dict[str, list[Any]] = {
            "console": [],
            "pageerror": [],
            "requestfailed": [],
        }

    def on(self, event: str, cb: Any) -> None:
        self._listeners[event].append(cb)

    def remove_listener(self, event: str, cb: Any) -> None:
        self._listeners[event].remove(cb)

    async def title(self) -> str:
        return self._title

    # Test helpers ------------------------------------------------------

    def emit(self, event: str, payload: Any) -> None:
        for cb in list(self._listeners[event]):
            cb(payload)

    def total_listeners(self) -> int:
        return sum(len(lst) for lst in self._listeners.values())


class TestDetachLifecycle:
    def test_attach_registers_three_listeners(self) -> None:
        page = _FakePage()
        PageStateCollector(page)
        assert page.total_listeners() == 3

    def test_detach_removes_all_listeners(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        assert page.total_listeners() == 3
        collector.detach()
        assert page.total_listeners() == 0

    def test_detach_is_idempotent(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        collector.detach()
        collector.detach()  # must not raise
        assert page.total_listeners() == 0

    def test_two_collectors_dont_leak_after_both_detach(self) -> None:
        # Regression: previously, listeners stacked forever on session reuse.
        page = _FakePage()
        c1 = PageStateCollector(page)
        c2 = PageStateCollector(page)
        assert page.total_listeners() == 6
        c1.detach()
        c2.detach()
        assert page.total_listeners() == 0

    def test_events_stop_reaching_collector_after_detach(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        collector.detach()
        page.emit("pageerror", "boom-after-detach")
        assert collector._page_errors == []


class TestEventFiltering:
    async def test_only_console_errors_captured(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        page.emit("console", _FakeConsoleMsg("log", "ignored 1"))
        page.emit("console", _FakeConsoleMsg("warn", "ignored 2"))
        page.emit("console", _FakeConsoleMsg("error", "captured 1"))
        page.emit("console", _FakeConsoleMsg("error", "captured 2"))
        state = await collector.snapshot_and_clear()
        assert state.console_errors == ["captured 1", "captured 2"]

    async def test_page_errors_captured(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        page.emit("pageerror", "Uncaught TypeError")
        state = await collector.snapshot_and_clear()
        assert state.page_errors == ["Uncaught TypeError"]

    async def test_network_failed_captured(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        page.emit("requestfailed", _FakeRequest("https://api.example/500"))
        state = await collector.snapshot_and_clear()
        assert state.network_failed == ["https://api.example/500"]

    async def test_malformed_console_msg_swallowed(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)

        class _Broken:
            pass  # no .type / .text

        # Must not raise
        page.emit("console", _Broken())
        state = await collector.snapshot_and_clear()
        assert state.console_errors == []


class TestBufferCaps:
    async def test_console_errors_cap_at_50(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        for i in range(75):
            page.emit("console", _FakeConsoleMsg("error", f"msg-{i}"))
        state = await collector.snapshot_and_clear()
        assert len(state.console_errors) == 50
        # First 50 kept; overflow dropped.
        assert state.console_errors[0] == "msg-0"
        assert state.console_errors[-1] == "msg-49"

    async def test_page_errors_cap_at_50(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        for i in range(75):
            page.emit("pageerror", f"err-{i}")
        state = await collector.snapshot_and_clear()
        assert len(state.page_errors) == 50

    async def test_network_failed_cap_at_50(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        for i in range(75):
            page.emit("requestfailed", _FakeRequest(f"https://x/{i}"))
        state = await collector.snapshot_and_clear()
        assert len(state.network_failed) == 50


class TestSnapshotAndClear:
    async def test_snapshot_clears_buffers(self) -> None:
        page = _FakePage()
        collector = PageStateCollector(page)
        page.emit("console", _FakeConsoleMsg("error", "first"))
        first = await collector.snapshot_and_clear()
        assert first.console_errors == ["first"]

        page.emit("console", _FakeConsoleMsg("error", "second"))
        second = await collector.snapshot_and_clear()
        assert second.console_errors == ["second"]

    async def test_snapshot_captures_title_and_url(self) -> None:
        page = _FakePage(title="My Page", url="https://example.com/x")
        collector = PageStateCollector(page)
        state = await collector.snapshot_and_clear()
        assert state.title == "My Page"
        assert state.url_after == "https://example.com/x"

    async def test_title_exception_falls_back_to_empty_string(self) -> None:
        class _FailingPage(_FakePage):
            async def title(self) -> str:
                raise RuntimeError("navigation in progress")

        page = _FailingPage()
        collector = PageStateCollector(page)
        state = await collector.snapshot_and_clear()
        assert state.title == ""
