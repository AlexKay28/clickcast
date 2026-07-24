"""Frame annotator — overlay ripples, labels, cursor trail, progress bar."""

from clickcast.annotate.annotator import AnnotateConfig, Annotator
from clickcast.annotate.pipeline import StepAnnotation, annotate_frames_dir

__all__ = ["AnnotateConfig", "Annotator", "StepAnnotation", "annotate_frames_dir"]
