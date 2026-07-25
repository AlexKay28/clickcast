"""Frame annotator — overlay ripples, labels, cursor trail, progress bar."""

from clickcast.annotate.annotator import (
    ActionsPanelStyle,
    AnnotateConfig,
    Annotator,
    CursorStyle,
    LabelStyle,
    ProgressStyle,
    RippleStyle,
)
from clickcast.annotate.pipeline import StepAnnotation, annotate_frames_dir

__all__ = [
    "ActionsPanelStyle",
    "AnnotateConfig",
    "Annotator",
    "CursorStyle",
    "LabelStyle",
    "ProgressStyle",
    "RippleStyle",
    "StepAnnotation",
    "annotate_frames_dir",
]
