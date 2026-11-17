"""Shared execution kernel.

Surfaces (CLI `auto`, CLI `run`, the `Reel` SDK, the MCP server) compose these
modules instead of re-implementing pipeline stages. Today the kernel owns the
media pipeline and sidecar assembly; the step runner moves here next.
"""

from clickcast.kernel.render import RenderPlan, RenderResult, render_reel
from clickcast.kernel.sidecar import (
    finalize_report,
    make_media,
    sidecar_path,
    transcript_media,
    write_media_sidecar,
    write_sidecar,
)

__all__ = [
    "RenderPlan",
    "RenderResult",
    "finalize_report",
    "make_media",
    "render_reel",
    "sidecar_path",
    "transcript_media",
    "write_media_sidecar",
    "write_sidecar",
]
