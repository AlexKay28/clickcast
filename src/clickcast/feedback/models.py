"""Pydantic models for the AI-feedback sidecar (schema v1 + v2 graph)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer


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


class PageNode(BaseModel):
    """A distinct URL visited during the tour — a node in the app graph.

    Identified by URL (canonical). ``first_seen_step`` / ``last_seen_step``
    span the index range in ``Report.steps`` during which the URL was the
    active page — lets a consumer find the frames + actions that touched it
    without re-scanning the whole step list.

    ``components`` names the ids of ``ComponentNode`` entries that were
    detected on this page. Empty in the v2-shipped-first slice — the
    landmark-detection pass that fingerprints reusable subtrees (nav,
    footer, sidebar) ships as a follow-up.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["page"] = "page"
    url: str
    title: str = ""
    dom_signature: str = ""
    first_seen_step: int = Field(ge=0)
    last_seen_step: int = Field(ge=0)
    components: list[str] = Field(default_factory=list)


class ComponentNode(BaseModel):
    """A stable DOM subtree that appears on ≥ 2 ``PageNode`` entries.

    Deduped across pages by ``dom_signature`` — same primary-nav rendered on
    /pricing and /docs surfaces exactly one ``ComponentNode``. Empty list in
    the v2-shipped-first slice (see :class:`PageNode.components`).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["component"] = "component"
    role: str
    selector: str = ""
    bbox: list[int] = Field(default_factory=list)
    dom_signature: str
    seen_on_nodes: list[str] = Field(default_factory=list)


class Edge(BaseModel):
    """A transition between two ``PageNode`` entries.

    Only ``transition_kind: "navigation"`` ships in the v2-first slice — the
    click caused ``page_state.url_after`` to change. ``reveal`` and
    ``dismiss`` classification requires DOM diffing across step boundaries
    and is deferred to a follow-up.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", serialization_alias="from")
    to: str
    via_step: int = Field(ge=0)
    selector: str | None = None
    transition_kind: Literal["navigation"] = "navigation"

    @model_serializer(mode="wrap")
    def _serialize_from_alias(self, handler: Any) -> dict[str, Any]:
        """Force ``from_`` → ``"from"`` on every serialization path.

        Pydantic's default ``model_dump`` returns field names, not aliases,
        so without this override the sidecar would contain ``"from_"``
        instead of the JSON-schema-declared ``"from"`` key.
        """
        data: dict[str, Any] = handler(self)
        if "from_" in data:
            data = {("from" if k == "from_" else k): v for k, v in data.items()}
        return data


class Graph(BaseModel):
    """Structural summary of the app the tour uncovered — v2 additive block.

    Attached to :class:`Report.graph` when the builder can extract at least
    one page node. Consumed by LLM agents to reason about "the shape of this
    app" rather than "what happened in this specific sequence".
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[PageNode | ComponentNode] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class Report(BaseModel):
    """AI-feedback sidecar — the primary contract for downstream agents.

    Deliberately does NOT set ``extra="forbid"`` at the top level so #29 Track
    C can add a ``graph`` block without a breaking schema change. The nested
    models above DO forbid extras — those shapes are stable.

    v2 (this release) adds the optional ``graph`` block. v1 sidecars round-
    trip through the v2 model unchanged: ``graph`` is optional and defaults
    to ``None`` when absent.
    """

    # No extra="forbid" here — see docstring above.

    schema_version: int = 2
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
    graph: Graph | None = None


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
