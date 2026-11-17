"""Shared execution kernel.

Surfaces (CLI `auto`, CLI `run`, the `Reel` SDK, the MCP server) compose these
modules instead of re-implementing pipeline stages. Today the kernel owns the
media pipeline; the step runner and sidecar writer move here next (#257).
"""

from clickcast.kernel.render import RenderPlan, RenderResult, render_reel

__all__ = ["RenderPlan", "RenderResult", "render_reel"]
