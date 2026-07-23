from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from pydantic import TypeAdapter, ValidationError

from clickcast.core.actions import (
    ClickStep,
    DblClickStep,
    GotoStep,
    HoverStep,
    PressStep,
    ScreenshotStep,
    ScrollStep,
    SelectStep,
    Step,
    TypeStep,
    WaitStep,
    execute,
)
from clickcast.core.session import Session


class TestStepModels:
    def test_goto_defaults(self) -> None:
        s = GotoStep(url="https://x")
        assert s.action == "goto"
        assert s.wait is None
        assert s.dwell == 0.0
        assert s.optional is False
        assert s.repeat == 1

    def test_click_requires_selector(self) -> None:
        with pytest.raises(ValidationError):
            ClickStep()  # type: ignore[call-arg]

    def test_extras_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GotoStep(url="https://x", nonsense="oops")  # type: ignore[call-arg]

    def test_repeat_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ClickStep(selector="#x", repeat=0)

    def test_discriminated_union_parses(self) -> None:
        adapter = TypeAdapter(Step)
        parsed = adapter.validate_python({"action": "click", "selector": "#x"})
        assert isinstance(parsed, ClickStep)
        assert parsed.selector == "#x"

    def test_discriminator_rejects_unknown_action(self) -> None:
        adapter = TypeAdapter(Step)
        with pytest.raises(ValidationError):
            adapter.validate_python({"action": "bogus", "selector": "#x"})

    def test_type_step_defaults(self) -> None:
        s = TypeStep(into="#i", text="hi")
        assert s.delay == 0.0

    def test_screenshot_step_defaults(self) -> None:
        s = ScreenshotStep()
        assert s.full_page is False
        assert s.path is None


FIXTURE_HTML = """<!DOCTYPE html>
<html><head><title>fixture</title></head>
<body>
  <button id="btn1">Click me</button>
  <input id="input" type="text">
  <select id="sel">
    <option value="a">A</option>
    <option value="b">B</option>
  </select>
  <div id="marker"></div>
  <div id="tall" style="height:2000px"></div>
  <div id="footer">Bottom</div>
  <script>
    document.getElementById('btn1').addEventListener('click', () => {
      document.getElementById('marker').textContent = 'clicked';
    });
    document.getElementById('btn1').addEventListener('dblclick', () => {
      document.getElementById('marker').textContent = 'dbl';
    });
  </script>
</body></html>
"""


@pytest_asyncio.fixture
async def loaded_session() -> AsyncIterator[Session]:
    async with Session(viewport=(800, 600)) as sess:
        await sess.page.set_content(FIXTURE_HTML)
        sess.page.set_default_timeout(3000)
        yield sess


@pytest.mark.integration
class TestActionIntegration:
    async def test_goto_returns_ok(self) -> None:
        async with Session(viewport=(400, 300)) as sess:
            r = await execute(
                GotoStep(url="data:text/html,<h1>hi</h1>", wait="load"),
                sess,
            )
        assert r.ok
        assert r.status == "ok"
        assert r.action == "goto"
        assert r.duration_ms > 0

    async def test_click_updates_marker_and_records_cursor(self, loaded_session: Session) -> None:
        r = await execute(ClickStep(selector="#btn1"), loaded_session)
        assert r.ok
        assert r.selector == "#btn1"
        assert r.cursor_xy is not None
        marker = await loaded_session.page.locator("#marker").text_content()
        assert marker == "clicked"

    async def test_dblclick(self, loaded_session: Session) -> None:
        r = await execute(DblClickStep(selector="#btn1"), loaded_session)
        assert r.ok
        marker = await loaded_session.page.locator("#marker").text_content()
        assert marker == "dbl"

    async def test_hover(self, loaded_session: Session) -> None:
        r = await execute(HoverStep(selector="#btn1"), loaded_session)
        assert r.ok
        assert r.cursor_xy is not None

    async def test_type_into_input(self, loaded_session: Session) -> None:
        r = await execute(TypeStep(into="#input", text="hello"), loaded_session)
        assert r.ok
        value = await loaded_session.page.locator("#input").input_value()
        assert value == "hello"

    async def test_press_with_focused_input(self, loaded_session: Session) -> None:
        await loaded_session.page.locator("#input").click()
        r = await execute(PressStep(key="A"), loaded_session)
        assert r.ok
        value = await loaded_session.page.locator("#input").input_value()
        assert "a" in value.lower()

    async def test_select_option(self, loaded_session: Session) -> None:
        r = await execute(SelectStep(into="#sel", value="b"), loaded_session)
        assert r.ok
        value = await loaded_session.page.locator("#sel").input_value()
        assert value == "b"

    async def test_scroll_by_pixels(self, loaded_session: Session) -> None:
        r = await execute(ScrollStep(by=200), loaded_session)
        assert r.ok

    async def test_scroll_to_selector(self, loaded_session: Session) -> None:
        r = await execute(ScrollStep(to="#footer"), loaded_session)
        assert r.ok
        assert r.selector == "#footer"

    async def test_scroll_without_target_fails(self, loaded_session: Session) -> None:
        r = await execute(ScrollStep(), loaded_session)
        assert not r.ok
        assert r.status == "failed"
        assert "either" in (r.error or "")

    async def test_wait_number(self, loaded_session: Session) -> None:
        r = await execute(WaitStep(wait=0.05), loaded_session)
        assert r.ok
        assert r.duration_ms >= 50

    async def test_wait_selector(self, loaded_session: Session) -> None:
        r = await execute(WaitStep(wait="#btn1"), loaded_session)
        assert r.ok

    async def test_screenshot_writes_file(self, loaded_session: Session, tmp_path) -> None:
        out = tmp_path / "shot.png"
        r = await execute(ScreenshotStep(path=str(out)), loaded_session)
        assert r.ok
        assert r.screenshot_path == out
        assert out.exists()
        assert out.read_bytes().startswith(b"\x89PNG")

    async def test_missing_selector_fails(self, loaded_session: Session) -> None:
        r = await execute(ClickStep(selector="#nope"), loaded_session)
        assert not r.ok
        assert r.status == "failed"
        assert r.selector == "#nope"
        assert r.error is not None

    async def test_missing_selector_optional_is_skipped(self, loaded_session: Session) -> None:
        r = await execute(ClickStep(selector="#nope", optional=True), loaded_session)
        assert r.ok
        assert r.status == "skipped"
        assert r.error is not None

    async def test_dwell_extends_duration(self, loaded_session: Session) -> None:
        r = await execute(ClickStep(selector="#btn1", dwell=0.1), loaded_session)
        assert r.ok
        assert r.duration_ms >= 100
