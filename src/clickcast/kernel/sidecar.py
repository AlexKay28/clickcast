"""One sidecar assembly path for every surface.

The v4 sidecar is clickcast's primary machine contract, and it used to be
assembled by four near-identical implementations:

* ``auto.py``: ``_make_media`` + ``_write_sidecar``
* ``cli.py``: a byte-identical copy of both
* ``reel.py``: ``_build_report_if_needed`` + ``_write_sidecar_from_report``
* ``mcp/server.py``: inline ``builder.build(...)`` + ``write_report(...)``

The copies drifted in ways users could see: MCP transcripts had a null ``url``,
empty per-step ``frames`` lists, a fabricated ``Media(path="", format="none")``
shape, and no way to redact tokens. Scenario runs (``clickcast run``) also had a
null ``url`` because their builder was never given one.

This module is the single implementation. Surfaces choose *when* to finalize
(``Reel`` needs the report for ``assertions()`` even with ``no_sidecar``) but
never *how*: media construction, report finalization, sidecar path derivation,
and write options all live here.
"""

from __future__ import annotations

import re
from pathlib import Path

from clickcast.encode import EncodeResult
from clickcast.feedback import Media, Report, ReportBuilder
from clickcast.feedback import write as write_report

__all__ = [
    "finalize_report",
    "make_media",
    "sidecar_path",
    "transcript_media",
    "write_media_sidecar",
    "write_sidecar",
]


def make_media(enc: EncodeResult, fps: int) -> Media:
    """Build the sidecar ``media`` block from an encoder result.

    ``fps`` is the encode rate the render kernel resolved to (capture rate, or
    the smoothed rate) — never re-derived from the encoder result, which does
    not carry an FPS field.
    """
    return Media(
        path=str(enc.path),
        format=enc.format,
        size_bytes=enc.size_bytes,
        frame_count=enc.frame_count,
        duration_s=enc.duration_s,
        fps=fps,
    )


def transcript_media(step_count: int) -> Media:
    """Media block for a live MCP session transcript.

    No reel is encoded from a live session, so the fields are zeroed — but the
    block is still present and schema-valid, which is what consumers of
    ``close_session(save_transcript=...)`` expect from a sidecar-shaped file.
    """
    return Media(
        path="",
        format="none",
        size_bytes=0,
        frame_count=step_count,
        duration_s=0.0,
        fps=1,
    )


def sidecar_path(out: str | Path) -> Path:
    """``tour.gif`` -> ``tour.gif.json`` (suffix appended, not replaced)."""
    path = Path(out)
    return path.with_suffix(path.suffix + ".json")


def finalize_report(builder: ReportBuilder | None, media: Media) -> Report | None:
    """Run ``builder.build(media)`` at most once, or return ``None`` without one.

    Split from :func:`write_sidecar` because ``ReportBuilder.build`` detaches
    listeners: ``Reel`` needs the finalized report for ``assertions()`` even
    when ``no_sidecar=True``, so it finalizes once and writes separately.
    """
    if builder is None:
        return None
    return builder.build(media)


def write_sidecar(
    out: str | Path,
    report: Report | None,
    *,
    target: str | Path | None = None,
    no_sidecar: bool = False,
    with_feedback: bool = False,
    redact_patterns: list[re.Pattern[str]] | None = None,
    strip_query_strings: bool = False,
) -> Path | None:
    """Serialize ``report`` next to ``out``; return the path written (or None).

    ``target`` overrides the derived ``<out>.json`` path — a live MCP session
    transcript names its own file, since no media file exists to derive from.
    ``no_sidecar`` and a missing report are both no-ops so call sites don't
    need their own guard. Redaction and feedback options are forwarded to
    :func:`clickcast.feedback.write` — the one place that implements them.
    """
    if no_sidecar or report is None:
        return None
    sidecar = Path(target) if target is not None else sidecar_path(out)
    write_report(
        report,
        sidecar,
        with_feedback=with_feedback,
        redact_patterns=redact_patterns,
        strip_query_strings=strip_query_strings,
    )
    return sidecar


def write_media_sidecar(
    out: str | Path,
    builder: ReportBuilder | None,
    enc: EncodeResult,
    fps: int,
    *,
    no_sidecar: bool = False,
    with_feedback: bool = False,
    redact_patterns: list[re.Pattern[str]] | None = None,
    strip_query_strings: bool = False,
) -> Path | None:
    """Build the media block, finalize the report, write the sidecar.

    The one call the batch surfaces (``auto``, ``run``) need. Returns the
    written path, or ``None`` when the sidecar was disabled.
    """
    report = finalize_report(builder, make_media(enc, fps))
    return write_sidecar(
        out,
        report,
        no_sidecar=no_sidecar,
        with_feedback=with_feedback,
        redact_patterns=redact_patterns,
        strip_query_strings=strip_query_strings,
    )
