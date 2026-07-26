"""Tests for `EvaluateStep` — schema + executor behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from pydantic import TypeAdapter, ValidationError

from clickcast.core.actions import EvaluateStep, Step, execute
from clickcast.core.session import Session

FIXTURE_HTML = """<!DOCTYPE html>
<html><body>
  <div id="marker">initial</div>
</body></html>
"""


@pytest_asyncio.fixture
async def loaded_session() -> AsyncIterator[Session]:
    async with Session(viewport=(600, 400)) as sess:
        await sess.page.set_content(FIXTURE_HTML)
        sess.page.set_default_timeout(3000)
        yield sess


class TestEvaluateStepModel:
    def test_defaults(self) -> None:
        s = EvaluateStep(expression="1 + 1")
        assert s.action == "evaluate"
        assert s.expression == "1 + 1"
        assert s.args == []

    def test_expression_is_required(self) -> None:
        with pytest.raises(ValidationError):
            EvaluateStep()  # type: ignore[call-arg]

    def test_extras_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            EvaluateStep(expression="1", nonsense=True)  # type: ignore[call-arg]

    def test_discriminated_union_parses(self) -> None:
        adapter = TypeAdapter(Step)
        parsed = adapter.validate_python({"action": "evaluate", "expression": "42"})
        assert isinstance(parsed, EvaluateStep)
        assert parsed.expression == "42"


@pytest.mark.integration
class TestEvaluateIntegration:
    async def test_evaluate_mutates_dom(self, loaded_session: Session) -> None:
        r = await execute(
            EvaluateStep(expression="document.getElementById('marker').textContent = 'set'"),
            loaded_session,
        )
        assert r.ok
        assert r.action == "evaluate"
        assert r.status == "ok"
        text = await loaded_session.page.locator("#marker").text_content()
        assert text == "set"

    async def test_evaluate_records_duration(self, loaded_session: Session) -> None:
        r = await execute(EvaluateStep(expression="1 + 1"), loaded_session)
        assert r.ok
        assert r.duration_ms > 0

    async def test_evaluate_with_args_splat(self, loaded_session: Session) -> None:
        r = await execute(
            EvaluateStep(
                expression=(
                    "([label]) => { document.getElementById('marker').textContent = label; }"
                ),
                args=["from-args"],
            ),
            loaded_session,
        )
        assert r.ok
        text = await loaded_session.page.locator("#marker").text_content()
        assert text == "from-args"

    async def test_evaluate_throws_marks_failed(self, loaded_session: Session) -> None:
        r = await execute(
            EvaluateStep(expression="() => { throw new Error('boom'); }"),
            loaded_session,
        )
        assert not r.ok
        assert r.status == "failed"
        assert r.error is not None
        assert "boom" in r.error

    async def test_evaluate_optional_absorbs_failure(self, loaded_session: Session) -> None:
        r = await execute(
            EvaluateStep(expression="() => { throw 'x' }", optional=True),
            loaded_session,
        )
        assert r.ok
        assert r.status == "skipped"
