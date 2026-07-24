"""Accumulator that turns a running pipeline into a :class:`Report`."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from clickcast.core.actions import ActionResult, BaseStep
from clickcast.feedback.collector import PageStateCollector
from clickcast.feedback.models import (
    DiscoveredElement,
    Media,
    Report,
    StepReport,
)


def _package_version() -> str:
    try:
        return version("clickcast")
    except PackageNotFoundError:
        return "0.0.0+unknown"


if TYPE_CHECKING:
    from clickcast.core.session import Session
    from clickcast.discovery import Element


__all__ = ["ReportBuilder"]


_COMMON_STEP_FIELDS = {"action", "label", "dwell", "optional", "repeat"}


class ReportBuilder:
    """Stateful builder — one instance per reel run."""

    def __init__(
        self,
        *,
        url: str | None = None,
        engine: str = "chromium",
        viewport: tuple[int, int] | list[int] | None = None,
    ) -> None:
        self._url = url
        self._engine = engine
        self._viewport: list[int] = list(viewport) if viewport else [1280, 800]
        self._discovered: list[DiscoveredElement] = []
        self._steps: list[StepReport] = []
        self._warnings: list[str] = []
        self._errors: list[str] = []

        self._collector: PageStateCollector | None = None
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._start_mono = time.monotonic()

    def attach(self, session: Session) -> None:
        """Wire the collector to the session's page — call once, at the start."""
        self._collector = PageStateCollector(session.page)

    def set_discovered(self, elements: list[Element]) -> None:
        self._discovered = [
            DiscoveredElement(
                selector=e.selector,
                role=e.role,
                text=e.text,
                bbox=list(e.bbox),
                score=e.score,
                source=e.source,
            )
            for e in elements
        ]

    async def record_step(
        self,
        *,
        index: int,
        step: BaseStep,
        result: ActionResult,
        frames: list[Path] | None = None,
    ) -> None:
        page_state = None
        if self._collector is not None:
            page_state = await self._collector.snapshot_and_clear()

        args = step.model_dump(exclude=_COMMON_STEP_FIELDS)

        self._steps.append(
            StepReport(
                index=index,
                action=step.action,
                args=args,
                status=result.status,
                duration_ms=result.duration_ms,
                frames=[Path(p).name for p in (frames or [])],
                label=step.label,
                cursor_xy=list(result.cursor_xy) if result.cursor_xy else None,
                page_state=page_state,
                error=result.error,
            )
        )

    def add_warning(self, msg: str) -> None:
        self._warnings.append(msg)

    def add_error(self, msg: str) -> None:
        self._errors.append(msg)

    def build(self, media: Media) -> Report:
        report = Report(
            clickcast_version=_package_version(),
            url=self._url,
            engine=self._engine,
            viewport=self._viewport,
            started_at=self._started_at,
            duration_s=time.monotonic() - self._start_mono,
            media=media,
            discovered_elements=self._discovered,
            steps=self._steps,
            warnings=self._warnings,
            errors=self._errors,
        )
        # Detach page listeners so the collector doesn't outlive the builder.
        # Idempotent — safe to call even if we were never attached.
        if self._collector is not None:
            self._collector.detach()
        return report
