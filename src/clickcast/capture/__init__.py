"""Frame capture pipeline."""

from clickcast.capture.recorder import CaptureBackend, FrameRef, Recorder
from clickcast.capture.screencast import (
    CapturedFrame,
    ScreencastSource,
    ScreencastUnavailable,
)

__all__ = [
    "CaptureBackend",
    "CapturedFrame",
    "FrameRef",
    "Recorder",
    "ScreencastSource",
    "ScreencastUnavailable",
]
