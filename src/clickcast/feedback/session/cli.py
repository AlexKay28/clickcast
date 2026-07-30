"""``clickcast feedback …`` — Typer sub-app for the session substrate.

Grouped as a sub-app rather than a set of top-level commands so the
namespace stays clean (``clickcast feedback start`` reads as a
sentence; ``clickcast feedback-start`` doesn't). Registered on the
root app in :mod:`clickcast.cli` via ``app.add_typer``.

v1 subcommands: ``start``, ``stop``, ``status``, ``list``, ``summary``.
The ``pause`` / ``resume`` / ``annotate`` / ``show`` / ``file``
subcommands listed in the #124 resolution plan are deferred to
follow-ups.
"""

from __future__ import annotations

from typing import Annotated

import typer

from clickcast.feedback.session.storage import SessionStore, default_store
from clickcast.feedback.session.summary import render_json, render_markdown, summarize

__all__ = ["feedback_app"]

feedback_app = typer.Typer(
    name="feedback",
    help="Long-form feedback loop: record clickcast usage, then summarize.",
    no_args_is_help=True,
    add_completion=False,
)


def _store() -> SessionStore:
    """Small indirection so tests can monkeypatch a temp-dir store."""
    return default_store()


@feedback_app.command("start", help="Begin recording a feedback session.")
def start(
    label: Annotated[
        str | None,
        typer.Option(
            "--label",
            help="Optional human-readable label for the session (shown in `feedback list`).",
        ),
    ] = None,
) -> None:
    store = _store()
    info = store.start(label=label)
    typer.echo(f"session started: {info.session_id}")
    if info.label:
        typer.echo(f"  label:   {info.label}")
    typer.echo(f"  started: {info.started_at}")


@feedback_app.command("stop", help="End the active feedback session.")
def stop() -> None:
    store = _store()
    info = store.stop()
    if info is None:
        typer.echo("no active session")
        raise typer.Exit(code=1)
    typer.echo(f"session stopped: {info.session_id}")
    if info.stopped_at:
        typer.echo(f"  stopped: {info.stopped_at}")


@feedback_app.command("status", help="Show the active session (or note there is none).")
def status() -> None:
    store = _store()
    active = store.active()
    if active is None:
        typer.echo("no active session")
        return
    typer.echo(f"active session: {active.session_id}")
    if active.label:
        typer.echo(f"  label:   {active.label}")
    if active.started_at:
        typer.echo(f"  started: {active.started_at}")


@feedback_app.command("list", help="List all recorded sessions (oldest first).")
def list_sessions() -> None:
    store = _store()
    sessions = store.list_sessions()
    if not sessions:
        typer.echo("no sessions recorded")
        return
    active = store.active()
    active_id = active.session_id if active else None
    for info in sessions:
        marker = "*" if info.session_id == active_id else " "
        label = f"  [{info.label}]" if info.label else ""
        stopped = info.stopped_at or "active"
        typer.echo(f"{marker} {info.session_id}  {info.started_at} → {stopped}{label}")


@feedback_app.command("summary", help="Render a summary of a recorded session.")
def summary_cmd(
    session: Annotated[
        str | None,
        typer.Option(
            "--session",
            help=(
                "Session id to summarize. Defaults to the active session, "
                "or the most recently started one if nothing is active."
            ),
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit JSON (sorted keys, deterministic) instead of Markdown.",
        ),
    ] = False,
) -> None:
    store = _store()
    session_id = _resolve_session_id(store, session)
    if session_id is None:
        typer.secho("no sessions available to summarize", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    info = store.load_info(session_id)
    if info is None:
        typer.secho(f"session not found: {session_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    events = store.load_events(session_id)
    summary = summarize(info, events)
    if as_json:
        typer.echo(render_json(summary), nl=False)
    else:
        typer.echo(render_markdown(summary), nl=False)


def _resolve_session_id(store: SessionStore, explicit: str | None) -> str | None:
    """Pick which session ``feedback summary`` should operate on.

    Order: explicit ``--session`` wins; then the active session; then
    the most-recently-started session on disk. Returning ``None``
    means "no sessions exist at all" — the caller surfaces that as an
    error.
    """
    if explicit:
        return explicit
    active = store.active()
    if active is not None:
        return active.session_id
    sessions = store.list_sessions()
    if not sessions:
        return None
    return sessions[-1].session_id
