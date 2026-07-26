"""Pydantic models for the AI-feedback sidecar (schema v1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Media(BaseModel):
    """Encoded reel metadata."""

    model_config = ConfigDict(extra="forbid")

    path: str
    format: str
    size_bytes: int = Field(ge=0)
    frame_count: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    fps: int = Field(ge=1)


class DiscoveredElement(BaseModel):
    """A single element returned by ``discover()`` at capture time."""

    model_config = ConfigDict(extra="forbid")

    selector: str
    role: str
    text: str
    bbox: list[int] = Field(min_length=4, max_length=4)
    score: float | int
    source: str


class PageState(BaseModel):
    """Post-action snapshot of the browser page."""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    url_after: str = ""
    console_errors: list[str] = Field(default_factory=list, max_length=50)
    page_errors: list[str] = Field(default_factory=list, max_length=50)
    network_failed: list[str] = Field(default_factory=list, max_length=50)


class StepReport(BaseModel):
    """One step's outcome + metadata."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: str  # ok | failed | skipped
    duration_ms: float = Field(ge=0)
    frames: list[str] = Field(default_factory=list)
    label: str | None = None
    cursor_xy: list[int] | None = None
    page_state: PageState | None = None
    error: str | None = None


class FeedbackTemplate(BaseModel):
    """Prompts that go into the prefilled issue body."""

    model_config = ConfigDict(extra="forbid")

    problem: str
    resolution_plan: str


class Feedback(BaseModel):
    """Machine-discoverable pointer for downstream AI agents (and humans) to
    file a GitHub issue about a tour that went wrong or a rough edge worth
    improving. Populated only when the writer is asked for it; absent from
    the sidecar otherwise.

    ``report_url`` / ``schema_url`` / ``docs_url`` / ``diagnostics_command``
    are the four fields specified by #40 for the AI-agent feedback loop.
    ``message`` / ``repo`` / ``issues_url`` / ``new_issue_url`` / ``template``
    are additive human-friendly context.
    """

    model_config = ConfigDict(extra="forbid")

    # #40 spec fields — the primary channel for stranded agents.
    report_url: str
    schema_url: str
    docs_url: str
    diagnostics_command: str
    # Additive human-friendly context (also usable by agents).
    message: str
    repo: str
    issues_url: str
    new_issue_url: str
    template: FeedbackTemplate


class Report(BaseModel):
    """AI-feedback sidecar — the primary contract for downstream agents.

    Deliberately does NOT set ``extra="forbid"`` at the top level so #29 Track
    C can add a ``graph`` block without a breaking schema change. The nested
    models above DO forbid extras — those shapes are stable.
    """

    # No extra="forbid" here — see docstring above.

    schema_version: int = 1
    clickcast_version: str
    url: str | None = None
    engine: str = "chromium"
    viewport: list[int] = Field(default_factory=lambda: [1280, 800])
    started_at: str  # ISO-8601 UTC
    duration_s: float = Field(ge=0)
    media: Media
    discovered_elements: list[DiscoveredElement] = Field(default_factory=list)
    steps: list[StepReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    feedback: Feedback | None = None


class StepAssertion(BaseModel):
    """One row of the CI-stable distillation — per-step regression signal.

    Deliberately narrow: only fields that survive re-recording of the same
    scenario against the same URL. Timing, frame paths, cursor coordinates,
    and resolved URLs are excluded on purpose (they change every run).
    """

    model_config = ConfigDict(extra="forbid")

    action: str
    label: str | None = None
    status: str  # ok | failed | skipped — mirrors StepReport.status
    console_error_count: int = Field(ge=0)
    page_error_count: int = Field(ge=0)
    network_failed_count: int = Field(ge=0)


class Assertions(BaseModel):
    """CI-stable distillation of a :class:`Report` — the assertion contract.

    Produced by :func:`clickcast.feedback.assertions.build_assertions` and
    consumed by :func:`~clickcast.feedback.assertions.diff_assertions`. The
    shape IS the contract, so ``extra="forbid"`` — a bug that quietly adds a
    new key fails at the model boundary rather than silently shifting every
    downstream baseline.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    step_count: int = Field(ge=0)
    steps: list[StepAssertion] = Field(default_factory=list)
