"""Frame annotator — overlay ripples, labels, cursor trail, progress bar."""

from clickcast.annotate.annotator import (
    ActionsPanelStyle,
    AnnotateConfig,
    Annotator,
    CursorStyle,
    LabelStyle,
    ProgressStyle,
    RippleStyle,
    TargetHighlightStyle,
)
from clickcast.annotate.cards import (
    CardStyle,
    SummaryStats,
    render_summary_card,
    render_title_card,
)
from clickcast.annotate.cursor_speed import DEFAULT_MAX_SPEED_PX_S, cap_cursor_speed
from clickcast.annotate.grid import GridConfig, draw_grid
from clickcast.annotate.interpolate import interpolate_cursor_motion
from clickcast.annotate.pipeline import (
    StepAnnotation,
    annotate_frames_dir,
    ripple_stage_for,
)
from clickcast.annotate.primitives import RegionAnnotation, load_annotations
from clickcast.annotate.reel_grammar import GrammarConfig, GrammarResult, apply_reel_grammar
from clickcast.annotate.smooth import SmoothConfig, SmoothResult, smooth_frames_dir
from clickcast.annotate.zoom import apply_zoom_on_click

__all__ = [
    "DEFAULT_MAX_SPEED_PX_S",
    "ActionsPanelStyle",
    "AnnotateConfig",
    "Annotator",
    "CardStyle",
    "CursorStyle",
    "GrammarConfig",
    "GrammarResult",
    "GridConfig",
    "LabelStyle",
    "ProgressStyle",
    "RegionAnnotation",
    "RippleStyle",
    "SmoothConfig",
    "SmoothResult",
    "StepAnnotation",
    "SummaryStats",
    "TargetHighlightStyle",
    "annotate_frames_dir",
    "apply_reel_grammar",
    "apply_zoom_on_click",
    "cap_cursor_speed",
    "draw_grid",
    "interpolate_cursor_motion",
    "load_annotations",
    "render_summary_card",
    "render_title_card",
    "ripple_stage_for",
    "smooth_frames_dir",
]
