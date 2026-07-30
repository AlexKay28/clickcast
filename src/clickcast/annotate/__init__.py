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
from clickcast.annotate.interpolate import interpolate_cursor_motion
from clickcast.annotate.pipeline import StepAnnotation, annotate_frames_dir
from clickcast.annotate.zoom import apply_zoom_on_click

__all__ = [
    "ActionsPanelStyle",
    "AnnotateConfig",
    "Annotator",
    "CardStyle",
    "CursorStyle",
    "LabelStyle",
    "ProgressStyle",
    "RippleStyle",
    "StepAnnotation",
    "SummaryStats",
    "TargetHighlightStyle",
    "annotate_frames_dir",
    "apply_zoom_on_click",
    "interpolate_cursor_motion",
    "render_summary_card",
    "render_title_card",
]
