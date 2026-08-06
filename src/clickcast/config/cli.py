"""``clickcast config …`` — Typer sub-app for persistent defaults.

Grouped as a sub-app rather than a single command taking a string action
argument so each subcommand gets its own ``--help``, shell completion
sees the valid names, and arg-requirement checks fall out of the
Typer signature instead of being hand-rolled per branch. See #177.

Registered on the root app in :mod:`clickcast.cli` via
``app.add_typer(config_app, name="config")``. The layout mirrors
:mod:`clickcast.feedback.session.cli`.
"""

from __future__ import annotations

from typing import Annotated, Any, NoReturn

import typer

from clickcast.config.config import (
    Config as ConfigModel,
)
from clickcast.config.config import (
    get_effective_value,
    set_user_value,
    user_config_path,
)
from clickcast.feedback.pointers import (
    DIAGNOSTICS_COMMAND,
    DOCS_URL,
    REPORT_URL,
    SCHEMA_URL,
    SKILL_URL,
)

__all__ = ["config_app"]

config_app = typer.Typer(
    name="config",
    help="Read / write persistent defaults.",
    no_args_is_help=True,
    add_completion=False,
)


# Kept in sync with :data:`clickcast.cli._FEEDBACK_EPILOG` so every
# subcommand's --help ends with the same AI-agent pointer footer.
# Duplicated (rather than imported from clickcast.cli) to avoid a
# circular import — cli.py imports :data:`config_app` from this module.
_FEEDBACK_EPILOG = "\n".join(
    [
        "",
        "For AI agents:",
        f"  skill guide:       {SKILL_URL}",
        f"  agent docs:        {DOCS_URL}",
        f"  report schema:     {SCHEMA_URL}",
        f"  file a bug report: {REPORT_URL}",
        f"  or run: {DIAGNOSTICS_COMMAND}",
    ]
)


def _die(msg: str, code: int = 1) -> NoReturn:
    """Local copy of :func:`clickcast.cli._die` so this module doesn't import
    from the top-level CLI (circular). Same behaviour: red stderr + non-zero
    exit."""
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _format_value(v: Any) -> str:
    """Render a config value for ``clickcast config list``.

    Handles the shapes that :func:`get_effective_value` can return (see #175):

    - non-empty ``list``: ``"; "``-joined, matching the friendlier env-var
      syntax accepted by the ``_parse_header`` validator for
      ``CLICKCAST_HEADER``. Pasting the output back into the env var
      round-trips.
    - empty ``list``: ``"(none)"`` — bare ``[]`` reads as "broken", not
      "unset".
    - ``None`` (unset ``Optional``): ``"(unset)"`` — bare ``None`` looks like
      the literal string.
    - anything else: ``str(v)``.
    """
    if isinstance(v, list):
        if not v:
            return "(none)"
        return "; ".join(str(item) for item in v)
    if v is None:
        return "(unset)"
    return str(v)


@config_app.command("path", help="Print the user-config TOML path.", epilog=_FEEDBACK_EPILOG)
def path_cmd() -> None:
    typer.echo(str(user_config_path()))


@config_app.command(
    "list",
    help="List every config key and its effective value.",
    epilog=_FEEDBACK_EPILOG,
)
def list_cmd() -> None:
    # Auto-width the key column so longer field names (``header_host``)
    # stay aligned with shorter ones (``fps``). Hardcoding 12 misaligned
    # any field whose name grew past that during later work — see #175.
    longest = max(len(k) for k in ConfigModel.model_fields)
    for k in sorted(ConfigModel.model_fields):
        typer.echo(f"  {k:<{longest}}  {_format_value(get_effective_value(k))}")


@config_app.command(
    "get",
    help="Print the effective value of a single config key.",
    epilog=_FEEDBACK_EPILOG,
)
def get_cmd(
    key: Annotated[str, typer.Argument(help="Config key.")],
) -> None:
    try:
        typer.echo(get_effective_value(key))
    except KeyError as e:
        _die(str(e))


@config_app.command(
    "set",
    help="Persist a config key to the user TOML.",
    epilog=_FEEDBACK_EPILOG,
)
def set_cmd(
    key: Annotated[str, typer.Argument(help="Config key.")],
    value: Annotated[str, typer.Argument(help="Value to persist.")],
) -> None:
    try:
        written_to = set_user_value(key, value)
    except (KeyError, ValueError) as e:
        _die(str(e))
    typer.echo(f"✔ {key} = {value}  ({written_to})")
