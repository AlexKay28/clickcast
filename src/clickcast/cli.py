"""Clickcast CLI — Typer app wiring every command promised in the README.

Command modules stay thin — each dispatches into `clickcast.core`,
`clickcast.scenario`, `clickcast.discovery`, or `clickcast.encode`. Business
logic lives in those subsystems, not here.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import subprocess
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Annotated, Any, TypeVar

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import typer
from platformdirs import user_config_dir

from clickcast import __version__
from clickcast.annotate import (
    AnnotateConfig,
    GridConfig,
    StepAnnotation,
    annotate_frames_dir,
)
from clickcast.auto import AutoConfig, run_tour
from clickcast.capture import Recorder
from clickcast.config import (
    Config as ConfigModel,
)
from clickcast.config import (
    load as load_config,
)
from clickcast.config.cli import config_app
from clickcast.core.actions import set_dump_elements
from clickcast.core.engines import EngineNotInstalledError, find_installed_engine
from clickcast.core.opts import BrowserOpts
from clickcast.core.session import Session
from clickcast.core.viewport import Viewport
from clickcast.discovery import AccessibleElement, Element, capture_accessibility_batch, discover
from clickcast.encode import encode
from clickcast.feedback import Media, ReportBuilder, feedback_pointer_lines
from clickcast.feedback import write as write_report
from clickcast.feedback.models import VisualDiffReport
from clickcast.feedback.pointers import (
    DIAGNOSTICS_COMMAND,
    DOCS_URL,
    REPORT_URL,
    SCHEMA_URL,
    SKILL_URL,
)
from clickcast.feedback.session.cli import feedback_app
from clickcast.feedback.session.storage import record_invocation_safe
from clickcast.feedback.visual_diff import DEFAULT_THRESHOLD as DEFAULT_VISUAL_DIFF_THRESHOLD
from clickcast.scenario import ScenarioError, load
from clickcast.scenario import run as run_scenario

_APP_NAME = "clickcast"

log = logging.getLogger("clickcast.auto")


def _setup_logging(verbose: int) -> None:
    """Configure the ``clickcast`` logger tree based on -v count.

    0 → WARNING (default), 1 → INFO (per-click + per-page traces),
    2+ → DEBUG (per-frame + internal wait details).

    Scoped fix for #174: only touches the ``"clickcast"`` logger, never the
    root logger. Library callers (apps that import clickcast and have their
    own JSON / structured / Sentry handlers on root) keep their setup
    intact. The CLI entrypoint :func:`main` installs a stderr handler on
    root when needed so CLI users still see log output.
    """
    if verbose <= 0:
        level = logging.WARNING
    elif verbose == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG
    logger = logging.getLogger("clickcast")
    logger.setLevel(level)
    # Let records propagate to whatever root logger the embedding app (or
    # our own :func:`main`) has configured — don't attach a handler here,
    # or every test / library caller would get one silently bolted on.


def _ensure_cli_root_handler() -> None:
    """Attach a stderr handler to root if the CLI is running bare.

    Called from :func:`main` only. When clickcast is used as a library the
    embedding app owns root logging; we must not touch it. In CLI mode,
    though, we still want log output on the terminal — so if root has no
    handlers at all, install a minimal stderr handler matching the format
    the pre-#174 ``basicConfig`` call used.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    # NOTSET (0) on root lets our scoped clickcast level through unmodified,
    # while any library logger the user hasn't opted into stays silent.
    root.setLevel(logging.NOTSET)


app = typer.Typer(
    name=_APP_NAME,
    help="Drive a browser through a website and return a reel + AI-readable feedback sidecar.",
    no_args_is_help=True,
    add_completion=False,
)


# #40 Track A: every subcommand ends its --help with these pointer lines so
# a stranded AI-agent user always sees where to file feedback. The `skill
# guide` line points at skill.md (added post-#166) — the long-form
# agent-facing usage guide with workflow patterns that don't fit in the
# 900-word `clickcast skill` brief.
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


# ==========================================================================
# Shared option types (Annotated makes them reusable across commands)
# ==========================================================================

ViewportArg = Annotated[str, typer.Option("--viewport", help="Viewport WxH, e.g. 1280x800.")]
Device = Annotated[
    str | None,
    typer.Option("--device", help="Device preset, e.g. 'iPhone 15'."),
]
Engine = Annotated[str, typer.Option("--engine", help="chromium | firefox | webkit.")]
Headful = Annotated[bool, typer.Option("--headful", help="Show a real browser window.")]
Slowmo = Annotated[int, typer.Option("--slowmo", help="Slow every action by N ms.")]
Dark = Annotated[bool, typer.Option("--dark", help="Emulate prefers-color-scheme: dark.")]
Lang = Annotated[str | None, typer.Option("--lang", help="Locale, e.g. en-US.")]
OutOpt = Annotated[str, typer.Option("--out", "-o", help="Output path.")]
FormatOpt = Annotated[
    str | None,
    typer.Option("--format", help="Override output format (gif | mp4 | webp | frames)."),
]
Quality = Annotated[int, typer.Option("--quality", help="Quality 1..30 (lower = better).")]
Loop = Annotated[int, typer.Option("--loop", help="Loop count (0 = infinite).")]
NoSidecar = Annotated[
    bool,
    typer.Option("--no-sidecar", help="Skip the AI-feedback JSON sidecar."),
]
WithFeedback = Annotated[
    bool,
    typer.Option(
        "--with-feedback",
        help=(
            "Attach a `feedback` block to the sidecar with the repo URL, a "
            "prefilled new-issue URL (title + body with environment context), "
            "and a short prompt template. Off by default — opt in when you "
            "want AI-agent consumers of the sidecar to know how to file bug "
            "reports and ideas."
        ),
    ),
]
# #110 — sidecar token-leak footgun fix. Repeatable regex flag scrubs matches
# with «redacted»; the boolean flag drops query strings entirely from URL fields.
RedactPattern = Annotated[
    list[str] | None,
    typer.Option(
        "--redact-pattern",
        help=(
            "Regex applied to every string in the sidecar; matches are replaced "
            "with «redacted». Repeatable. Use to blot out auth-bypass tokens "
            "leaked into recorded URLs (Vercel / Cloudflare / Netlify previews). "
            "Example: --redact-pattern 'x-vercel-protection-bypass=[^&]+'."
        ),
    ),
]
StripQueryStrings = Annotated[
    bool,
    typer.Option(
        "--strip-query-strings",
        help=(
            "Drop the query string from every recorded URL in the sidecar. "
            "Coarse but safe default for auth-bypassed preview flows — turn on "
            "when you don't need query params to reproduce the tour."
        ),
    ),
]
Fps = Annotated[int, typer.Option("--fps", help="Frames per second.")]
Verbose = Annotated[
    int,
    typer.Option("--verbose", "-v", count=True, help="Increase output verbosity."),
]
DumpElements = Annotated[
    bool,
    typer.Option(
        "--dump-elements",
        help=(
            "On step failure, dump the full discover() list to stderr in "
            "addition to the top-5 candidate hints. Off by default."
        ),
    ),
]
# #166: internal / SSO-protected sites need TLS bypass + scoped auth headers.
Insecure = Annotated[
    bool,
    typer.Option(
        "--insecure",
        help=(
            "Ignore TLS certificate errors (self-signed / private CA). "
            "Use for internal hosts whose cert chain the bundled Chromium "
            "does not trust. Same idea as `curl --insecure`."
        ),
    ),
]
Header = Annotated[
    list[str] | None,
    typer.Option(
        "--header",
        "-H",
        help=(
            'Extra request header, `"Name: value"`. Repeatable. Without '
            "`--header-host`, sent to EVERY request the page makes — "
            "including CDN and analytics subresources. For an auth token, "
            "scope it with `--header-host`."
        ),
    ),
]
HeaderHost = Annotated[
    str | None,
    typer.Option(
        "--header-host",
        help=(
            "Restrict `--header` delivery to requests whose hostname equals "
            "this host (or is a dotted subdomain of it). Prevents leaking "
            "bearer tokens to third-party origins the page loads from."
        ),
    ),
]
# #171: pixel-grid overlay flags. Off by default across every command that
# accepts them (`auto`, `run`); the `shot` command exposes the same flags
# so the help stays discoverable, but v1 does not composite the grid onto
# a single screenshot — noted in the help text. See `_do_shot`.
Grid = Annotated[
    bool,
    typer.Option(
        "--grid",
        help=(
            "Overlay a pixel-grid on every frame so AI-agent consumers can "
            "measure distances by reading coordinate labels instead of "
            "counting pixels. Off by default."
        ),
    ),
]
GridPitch = Annotated[
    int,
    typer.Option(
        "--grid-pitch",
        help="Major-line spacing in px (default 100). Minor lines at pitch/10.",
    ),
]
GridColor = Annotated[
    str,
    typer.Option(
        "--grid-color",
        help="RGBA hex, e.g. #FFFFFF33 (white @ 20% opacity, the default).",
    ),
]
GridStyle = Annotated[
    str,
    typer.Option(
        "--grid-style",
        help=(
            "full | ruler (default full). full = major+minor gridlines + edge "
            "labels; ruler = edge labels only, no gridlines."
        ),
    ),
]
# #151 (AI-4) — machine-readable summary line after the shipped prose
# summary. JSONL-friendly (one object per line, no trailing prose).
EmitEvents = Annotated[
    bool,
    typer.Option(
        "--emit-events",
        help=(
            "After the human-readable tail line, print one JSON object on "
            'its own line to stdout ({"event": "tour_complete", ...}). '
            "JSONL-friendly for agents that regex-scrape the shipped prose. "
            "Off by default."
        ),
    ),
]


# ==========================================================================
# Helpers
# ==========================================================================


def _parse_viewport(v: str) -> tuple[int, int]:
    """Typer-friendly shim: wraps :meth:`Viewport.parse` and remaps the
    ``ValueError`` to ``typer.BadParameter`` so Click formats the message
    the way the rest of the CLI does. Returns a tuple for the existing
    session-kwargs shape (Session accepts tuples too)."""
    try:
        return Viewport.parse(v).as_tuple()
    except (TypeError, ValueError) as e:
        raise typer.BadParameter(f"invalid viewport {v!r}; expected WxH") from e


def _die(msg: str, code: int = 1) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _install_engine(engine_list: list[str], *, with_deps: bool) -> int:
    """Run `playwright install` for `engine_list`. Shared by the `install`
    command and the missing-engine pre-flight prompt below."""
    # Always use the venv's playwright module — a system-wide `playwright`
    # binary on PATH could point at a different playwright version than the
    # one clickcast imports, causing "Executable doesn't exist" errors when
    # the runtime tries to launch a browser it never downloaded. (#176)
    cmd = [sys.executable, "-m", "playwright", "install"]
    if with_deps:
        cmd.append("--with-deps")
    cmd.extend(engine_list)
    typer.echo(f"→ {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    return result.returncode


_T = TypeVar("_T")


def _handle_missing_engine(exc: EngineNotInstalledError) -> None:
    """Called once when a browser-launching command hits a missing engine.

    Interactive terminal: offer to install it right now — the point is that
    a user's very first real command self-heals instead of dead-ending on a
    Playwright traceback. Non-interactive (CI, piped input): never prompt,
    just fail with the exact fix command.
    """
    engine = exc.engine
    fix_cmd = f"clickcast install --with-deps {engine}"
    if not sys.stdin.isatty():
        _die(f"{engine} isn't installed. Run: {fix_cmd}")
    typer.secho(f"⚠ {engine} isn't installed.", fg=typer.colors.YELLOW, err=True)
    if not typer.confirm(f"Install it now? ({fix_cmd})", default=True):
        _die(f"{engine} isn't installed. Run: {fix_cmd}")
    code = _install_engine([engine], with_deps=True)
    if code != 0:
        _die(f"install failed (exit {code}). Run manually: {fix_cmd}")


def _run(factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """`asyncio.run`, but a missing browser engine gets one self-heal retry
    instead of a raw Playwright traceback. `factory` (not a bare coroutine)
    because a coroutine object can only be awaited once — a retry needs a
    fresh one."""
    try:
        return asyncio.run(factory())
    except EngineNotInstalledError as exc:
        _handle_missing_engine(exc)
        return asyncio.run(factory())


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


def _parse_header_flags(raw: list[str] | None) -> dict[str, str]:
    """Parse ``--header "Name: value"`` occurrences into a header dict.

    Duplicate names win last (matches Playwright's own extra_http_headers
    semantics). Malformed entries (no ``:``) raise :class:`typer.BadParameter`
    so the user gets the same red-message treatment as other CLI misuse.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for entry in raw:
        if ":" not in entry:
            raise typer.BadParameter(f"--header must be 'Name: value', got {entry!r}")
        name, _, value = entry.partition(":")
        name = name.strip()
        value = value.strip()
        if not name:
            raise typer.BadParameter(f"--header has empty name: {entry!r}")
        out[name] = value
    return out


def _session_kwargs(
    engine: str,
    viewport: str,
    device: str | None,
    headful: bool,
    lang: str | None,
    dark: bool,
    slowmo: int = 0,
    *,
    insecure: bool = False,
    extra_headers: dict[str, str] | None = None,
    header_host: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    """Kept as the CLI's ``BrowserOpts`` factory: turns the flat CLI-flag
    args into a :class:`~clickcast.core.opts.BrowserOpts` and returns its
    :meth:`~clickcast.core.opts.BrowserOpts.to_session_kwargs` shape.
    Since #97 the field list lives in ``BrowserOpts``; this function is
    only glue for turning ``typer`` params into that dataclass. #166 added
    ``insecure`` / ``extra_headers`` / ``header_host``; ``proxy`` also
    flows through now (was silently dropped by ``to_session_kwargs``)."""
    return BrowserOpts(
        engine=engine,
        viewport=Viewport.parse(viewport),
        device=device,
        headful=headful,
        lang=lang,
        dark=dark,
        slowmo=slowmo,
        proxy=proxy,
        insecure=insecure,
        extra_headers=dict(extra_headers or {}),
        header_host=header_host,
    ).to_session_kwargs()


def _make_media(enc: Any, fps: int) -> Media:
    return Media(
        path=str(enc.path),
        format=enc.format,
        size_bytes=enc.size_bytes,
        frame_count=enc.frame_count,
        duration_s=enc.duration_s,
        fps=fps,
    )


def _write_sidecar(
    out: Path,
    no_sidecar: bool,
    builder: ReportBuilder | None,
    media: Media,
    *,
    with_feedback: bool = False,
    redact_patterns: list[re.Pattern[str]] | None = None,
    strip_query_strings: bool = False,
) -> Path | None:
    if no_sidecar or builder is None:
        return None
    sidecar = out.with_suffix(out.suffix + ".json")
    report = builder.build(media)
    write_report(
        report,
        sidecar,
        with_feedback=with_feedback,
        redact_patterns=redact_patterns,
        strip_query_strings=strip_query_strings,
    )
    return sidecar


def _build_grid_config(
    enabled: bool,
    pitch: int,
    color: str,
    style: str,
) -> GridConfig | None:
    """Turn the CLI grid flags into a :class:`GridConfig` (or ``None``).

    Validates ``pitch`` and ``style`` early with :class:`typer.BadParameter`
    so bad input surfaces with the same red-message treatment as other CLI
    misuse. Returns ``None`` when the grid is disabled — mirrors the
    ``AnnotateConfig.grid`` field, whose ``None`` means "off, don't build".
    """
    if not enabled:
        return None
    if pitch <= 0:
        raise typer.BadParameter(f"--grid-pitch must be > 0, got {pitch}")
    if style not in ("full", "ruler"):
        raise typer.BadParameter(f"--grid-style must be 'full' or 'ruler', got {style!r}")
    # Validate the color string here (rather than deferring to the annotator)
    # so bad input fails BEFORE the browser launches — much nicer feedback loop.
    from clickcast.annotate.grid import parse_rgba_hex

    try:
        parse_rgba_hex(color)
    except ValueError as e:
        raise typer.BadParameter(f"--grid-color: {e}") from e
    return GridConfig(enabled=True, pitch=pitch, color=color, style=style)  # type: ignore[arg-type]


def _compile_redact_patterns(raw: list[str] | None) -> list[re.Pattern[str]]:
    """Compile ``--redact-pattern`` values, dying with a user-friendly error
    when a pattern doesn't parse. Returns an empty list when nothing was passed
    so callers can pass it through unconditionally.
    """
    if not raw:
        return []
    compiled: list[re.Pattern[str]] = []
    for src in raw:
        try:
            compiled.append(re.compile(src))
        except re.error as e:
            raise typer.BadParameter(f"invalid --redact-pattern {src!r}: {e}") from e
    return compiled


# ==========================================================================
# Top-level
# ==========================================================================


def _build_cli_command_params() -> dict[str, frozenset[str]]:
    """Introspect ``app.registered_commands`` once and cache the {command → Config-relevant param names} table.

    Shipped separately from :func:`_config_default_map` so the expensive
    ``inspect.signature`` + ``app.registered_commands`` walk only runs at
    import time (see #151 REF-4). Per-invocation, ``_config_default_map``
    just projects the current :class:`Config` values onto this frozen table
    — no signature introspection, no command-registry walk. ``ctx:
    typer.Context`` and other non-Config params fall out naturally because
    they aren't in :attr:`Config.model_fields`.
    """
    field_names = frozenset(ConfigModel.model_fields)
    out: dict[str, frozenset[str]] = {}
    for cmd_info in app.registered_commands:
        callback = cmd_info.callback
        if callback is None:
            # `@app.command` without a body — nothing to introspect. Typer
            # itself would never register such a command, but the type says
            # Optional so guard it.
            continue
        # Typer defers name derivation until CLI-registration time, so
        # ``cmd_info.name`` is often ``None``; fall back to the callback name
        # (which is what Typer itself does under the hood).
        cmd_name = cmd_info.name or callback.__name__
        param_names = {p.name for p in inspect.signature(callback).parameters.values()}
        out[cmd_name] = frozenset(param_names & field_names)
    return out


# Cached at import time — see #151 REF-4. The set of {command → Config-keys}
# never changes at runtime (commands are registered via decorators before
# `main()` runs), so redoing `inspect.signature` on every invocation was
# pure waste. Populated at module load, right after every `@app.command`
# has run.
_CLI_COMMAND_PARAMS: dict[str, frozenset[str]] = {}


def _config_default_map() -> dict[str, dict[str, Any]]:
    """Build Click's per-command `default_map` from the layered Config.

    Load-once per invocation: env vars + project TOML + user TOML resolved
    now, then Click uses these as fallbacks unless an explicit CLI flag wins.

    Since #151 (REF-4), the {command → Config-relevant param names} table is
    cached at import time in ``_CLI_COMMAND_PARAMS``. This function only
    projects the freshly-loaded Config values onto that frozen table — no
    ``inspect.signature`` or ``app.registered_commands`` walk per call.
    """
    # See #151 (PERF-3): the old broad `except Exception: return {}` swallowed
    # TOML parse errors silently, leaving users mystified about why their
    # config was ignored. Surface TOML parse errors on stderr with the same
    # ⚠ marker as feedback/advisories.py — _read_toml prepends the file
    # path to the error message so the warning identifies which file.
    # OSError stays silent because a missing config file is the normal
    # "no config yet" case; any other unexpected exception still falls
    # through to defaults so `--help` and friends never brick.
    try:
        cfg = load_config()
    except tomllib.TOMLDecodeError as e:
        typer.secho(
            f"⚠ clickcast.toml: TOML parse error — using defaults ({e})",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return {}
    except OSError:
        return {}
    except Exception:
        return {}
    fields = cfg.model_dump()
    return {
        cmd_name: {k: fields[k] for k in params if k in fields}
        for cmd_name, params in _CLI_COMMAND_PARAMS.items()
    }


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Show version and exit.",
            is_eager=True,
            callback=_version_callback,
        ),
    ] = False,
) -> None:
    ctx.default_map = _config_default_map()


# ==========================================================================
# clickcast auto
# ==========================================================================


# Speed presets — one flag (--pace) sets fps + dwell together so users don't
# have to think about frame math. Explicit --fps / --dwell still override.
_PACE_TABLE: dict[str, tuple[int, float]] = {
    # name: (fps, dwell)
    "fast": (15, 0.15),
    "natural": (12, 0.4),
    "slow": (10, 0.7),
    "onboarding": (8, 1.2),
}


@app.command(help="Auto-discover interactive elements and record a tour.", epilog=_FEEDBACK_EPILOG)
def auto(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Target URL.")],
    out: OutOpt = "reel.gif",
    max_steps: Annotated[
        int,
        typer.Option(
            "--max-steps",
            "-N",
            help=(
                "Total click budget across the whole tour (sum of clicks on every visited page)."
            ),
        ),
    ] = 15,
    max_pages: Annotated[
        int,
        typer.Option(
            "--max-pages",
            help=(
                "Cap on how many pages the tour visits, including the start URL. "
                "Set to 1 to disable multi-page exploration."
            ),
        ),
    ] = 5,
    max_duration: Annotated[
        float,
        typer.Option(
            "--max-duration",
            help=(
                "Hard wall-time cap on the whole tour, in seconds. When hit, BFS "
                "stops immediately and whatever frames were captured get encoded."
            ),
        ),
    ] = 120.0,
    click_timeout: Annotated[
        float,
        typer.Option(
            "--click-timeout",
            help=(
                "Per-click timeout in seconds. Overrides Playwright's 30s default so "
                "one stuck click can't stall the tour. Applies to click/hover/type/etc."
            ),
        ),
    ] = 5.0,
    traversal: Annotated[
        str,
        typer.Option(
            "--traversal",
            help=(
                "Queue policy for the URL queue: 'dfs' (default, follow the most "
                "recently discovered link first — coherent narrative reels) or "
                "'bfs' (visit every top-level nav destination before going deeper "
                "— better for site-map style coverage under a tight page cap). "
                "Ignored when --seed-url is set (order is exactly what you gave)."
            ),
        ),
    ] = "dfs",
    seed_url: Annotated[
        list[str] | None,
        typer.Option(
            "--seed-url",
            help=(
                "Additional URL to include in the tour. Pass multiple times, "
                "e.g. --seed-url /pricing --seed-url /docs. When set, the tour "
                "visits exactly the initial URL + these seeds (in order) and "
                "does NOT auto-enqueue navigation destinations discovered during "
                "clicks. For AI agents that want to drive a deterministic path."
            ),
        ),
    ] = None,
    pace: Annotated[
        str,
        typer.Option(
            "--pace",
            help=(
                "Speed preset: fast | natural | slow | onboarding. Sets --fps + "
                "--dwell together so users don't have to think about frame math. "
                "Explicit --fps / --dwell still win when set."
            ),
        ),
    ] = "natural",
    zoom_on_click: Annotated[
        float,
        typer.Option(
            "--zoom-on-click",
            help=(
                "Crop-and-scale post-click frames around the click point by this "
                "factor (e.g. 2.5 = 2.5x zoom). 0 or omitted = disabled. "
                "Zoomed frames re-render at viewport size; overlays land at the "
                "correct coords for the zoomed image."
            ),
        ),
    ] = 0.0,
    for_humans: Annotated[
        bool,
        typer.Option(
            "--for-humans",
            help=(
                "Composite flag: flip several sub-flags to human-friendly "
                "defaults (--pace onboarding, --zoom-on-click 2.5, "
                "--highlight-target, --title-card, --summary-card) so the "
                "reel is legible to a person watching without the sidecar. "
                "Explicit flags always win. See #129."
            ),
        ),
    ] = False,
    highlight_target: Annotated[
        bool,
        typer.Option(
            "--highlight-target",
            help=(
                "Draw a soft pulsing ring around each click target on the "
                "pre-click hold-frames, so a human eye locks on before the "
                "ripple fires. Off by default; on when --for-humans is set. "
                "See #129 Track A."
            ),
        ),
    ] = False,
    title_card: Annotated[
        bool,
        typer.Option(
            "--title-card",
            help=(
                "Prepend a title card ('clickcast tour · <host>') to the "
                "reel — masks any pre-first-paint white flash and gives "
                "human viewers a labelled entry beat. See #129 Track E."
            ),
        ),
    ] = False,
    summary_card: Annotated[
        bool,
        typer.Option(
            "--summary-card",
            help=(
                "Append a summary card (pages · clicks · duration) to the "
                "end of the reel — a stats-summary tail so human viewers "
                "know the tour concluded. See #129 Track E."
            ),
        ),
    ] = False,
    dwell: Annotated[
        float, typer.Option("--dwell", help="Seconds to hold after each action.")
    ] = 1.0,
    initial_wait: Annotated[
        float,
        typer.Option(
            "--initial-wait",
            help="Seconds to hold after networkidle before interacting (SPA hydration).",
        ),
    ] = 2.0,
    viewport: ViewportArg = "1280x800",
    device: Device = None,
    engine: Engine = "chromium",
    headful: Headful = False,
    lang: Lang = None,
    dark: Dark = False,
    fps: Fps = 12,
    format: FormatOpt = None,
    quality: Quality = 8,
    loop: Loop = 0,
    no_sidecar: NoSidecar = False,
    with_feedback: WithFeedback = False,
    redact_pattern: RedactPattern = None,
    strip_query_strings: StripQueryStrings = False,
    verbose: Verbose = 0,
    dump_elements: DumpElements = False,
    emit_events: EmitEvents = False,
    insecure: Insecure = False,
    header: Header = None,
    header_host: HeaderHost = None,
    grid: Grid = False,
    grid_pitch: GridPitch = 100,
    grid_color: GridColor = "#FFFFFF33",
    grid_style: GridStyle = "full",
) -> None:
    _setup_logging(verbose)
    set_dump_elements(dump_elements)
    if traversal not in ("dfs", "bfs"):
        _die(f"--traversal must be 'dfs' or 'bfs', got {traversal!r}")
    if pace not in _PACE_TABLE:
        _die(f"--pace must be one of {sorted(_PACE_TABLE)}, got {pace!r}")
    compiled_redacts = _compile_redact_patterns(redact_pattern)
    extra_headers = _parse_header_flags(header)
    grid_cfg = _build_grid_config(grid, grid_pitch, grid_color, grid_style)

    # --for-humans composite: flip the sub-flags to human defaults, but ONLY
    # for sub-flags the user did not explicitly type on the command line.
    # Same precedence pattern as --pace: explicit CLI flag always wins over
    # the preset, config-driven / default values are treated as overridable.
    if for_humans:
        if not _is_explicit(ctx, "pace"):
            pace = "onboarding"
        if not _is_explicit(ctx, "zoom_on_click"):
            zoom_on_click = 2.5
        if not _is_explicit(ctx, "highlight_target"):
            highlight_target = True
        if not _is_explicit(ctx, "title_card"):
            title_card = True
        if not _is_explicit(ctx, "summary_card"):
            summary_card = True

    # Pace presets set fps + dwell defaults; explicit CLI flags still win.
    # `_is_explicit` returns True only when the user typed --fps / --dwell —
    # config-driven or default values are treated as overridable by the preset.
    preset_fps, preset_dwell = _PACE_TABLE[pace]
    if not _is_explicit(ctx, "fps"):
        fps = preset_fps
    if not _is_explicit(ctx, "dwell"):
        dwell = preset_dwell
    log.info(
        "resolved pace=%s → fps=%d dwell=%.2fs for_humans=%s",
        pace,
        fps,
        dwell,
        for_humans,
    )

    _run(
        lambda: _do_auto(
            url=url,
            out=out,
            max_steps=max_steps,
            max_pages=max_pages,
            max_duration=max_duration,
            click_timeout_ms=int(click_timeout * 1000),
            traversal=traversal,
            seed_urls=list(seed_url) if seed_url else [],
            dwell=dwell,
            initial_wait=initial_wait,
            session_kwargs=_session_kwargs(
                engine,
                viewport,
                device,
                headful,
                lang,
                dark,
                insecure=insecure,
                extra_headers=extra_headers,
                header_host=header_host,
            ),
            fps=fps,
            format_=format,
            quality=quality,
            loop=loop,
            no_sidecar=no_sidecar,
            with_feedback=with_feedback,
            redact_patterns=compiled_redacts,
            strip_query_strings=strip_query_strings,
            zoom_on_click_factor=(zoom_on_click if zoom_on_click > 1.0 else None),
            target_highlight=highlight_target,
            title_card=title_card,
            summary_card=summary_card,
            emit_events=emit_events,
            grid=grid_cfg,
        )
    )


# The auto engine lives in `clickcast.auto`. This shim exists purely so tests
# that patch `clickcast.cli._do_auto` (5 test files) keep working after the
# extraction. New callers should import `run_tour` from `clickcast.auto`.
async def _do_auto(
    *,
    url: str,
    out: str,
    max_steps: int,
    max_pages: int,
    max_duration: float,
    click_timeout_ms: int,
    traversal: str = "dfs",
    seed_urls: list[str] | None = None,
    dwell: float,
    initial_wait: float,
    session_kwargs: dict[str, Any],
    fps: int,
    format_: str | None,
    quality: int,
    loop: int,
    no_sidecar: bool,
    with_feedback: bool = False,
    redact_patterns: list[re.Pattern[str]] | None = None,
    strip_query_strings: bool = False,
    zoom_on_click_factor: float | None = None,
    target_highlight: bool = False,
    title_card: bool = False,
    summary_card: bool = False,
    emit_events: bool = False,
    grid: GridConfig | None = None,
) -> None:
    # The AutoConfig.target_highlight flag drives recorder-side padding +
    # bbox lookup; the annotator itself needs its own toggle so the layer
    # actually renders. Keep the two in lockstep here so a shipped caller
    # that flips one always gets the other.
    annotate = AnnotateConfig(target_highlight=target_highlight, grid=grid)
    await run_tour(
        AutoConfig(
            url=url,
            out=out,
            max_steps=max_steps,
            max_pages=max_pages,
            max_duration=max_duration,
            click_timeout_ms=click_timeout_ms,
            traversal=traversal,
            seed_urls=list(seed_urls or []),
            dwell=dwell,
            initial_wait=initial_wait,
            session_kwargs=session_kwargs,
            fps=fps,
            format=format_,
            quality=quality,
            loop=loop,
            no_sidecar=no_sidecar,
            with_feedback=with_feedback,
            redact_patterns=list(redact_patterns or []),
            strip_query_strings=strip_query_strings,
            zoom_on_click_factor=zoom_on_click_factor,
            annotate=annotate,
            target_highlight=target_highlight,
            title_card=title_card,
            summary_card=summary_card,
            emit_events=emit_events,
        )
    )


# ==========================================================================
# clickcast run
# ==========================================================================


@app.command(help="Run a YAML scenario end-to-end.", epilog=_FEEDBACK_EPILOG)
def run(
    ctx: typer.Context,
    scenario_path: Annotated[Path, typer.Argument(help="Path to a scenario file.")],
    out: Annotated[
        str | None, typer.Option("--out", "-o", help="Override scenario meta.out.")
    ] = None,
    format: FormatOpt = None,
    headful: Headful = False,
    slowmo: Slowmo = 0,
    url: Annotated[
        str | None,
        typer.Option(
            "--url",
            help=(
                "Override the URL of the first `goto` step in the scenario. "
                "Handy for pointing an existing scenario at a different "
                "environment (staging, PR preview, localhost) without editing "
                "the YAML. Wins over `--var URL=...` and any URL baked into "
                "the scenario. Only rewrites the FIRST goto — later goto "
                "steps stay put (they may be intra-app navigation)."
            ),
        ),
    ] = None,
    var: Annotated[
        list[str] | None,
        typer.Option("--var", help="Inject a scenario variable as key=value."),
    ] = None,
    no_sidecar: NoSidecar = False,
    with_feedback: WithFeedback = False,
    redact_pattern: RedactPattern = None,
    strip_query_strings: StripQueryStrings = False,
    verbose: Verbose = 0,
    dump_elements: DumpElements = False,
    emit_events: EmitEvents = False,
    insecure: Insecure = False,
    header: Header = None,
    header_host: HeaderHost = None,
    grid: Grid = False,
    grid_pitch: GridPitch = 100,
    grid_color: GridColor = "#FFFFFF33",
    grid_style: GridStyle = "full",
) -> None:
    set_dump_elements(dump_elements)
    compiled_redacts = _compile_redact_patterns(redact_pattern)
    grid_cfg = _build_grid_config(grid, grid_pitch, grid_color, grid_style)
    variables: dict[str, str] = {}
    for pair in var or []:
        if "=" not in pair:
            raise typer.BadParameter(f"--var must be key=value, got {pair!r}")
        k, v = pair.split("=", 1)
        variables[k] = v

    try:
        scenario = load(scenario_path, variables=variables or None)
    except ScenarioError as e:
        _die(f"scenario: {e}")

    # Precedence for `run`: explicit CLI flag > scenario meta > Config > default.
    # Values arriving here come from one of two sources:
    #   - COMMANDLINE : user explicitly typed --flag  → wins over meta
    #   - DEFAULT / DEFAULT_MAP / ENVIRONMENT : filled through Config → meta wins
    # Compared on `.name` so we don't need to import Typer's vendored Click.
    meta = scenario.meta.model_copy(deep=True)
    final_out = out or meta.out
    if _is_explicit(ctx, "headful"):
        meta.browser.headful = headful
    if _is_explicit(ctx, "slowmo"):
        meta.browser.slowmo = slowmo
    # #166: `--insecure` / `--header` / `--header-host` follow the same
    # "explicit CLI flag wins over scenario meta" pattern as headful/slowmo
    # above. Values from Config layers (env / TOML) also arrive here, but
    # `_is_explicit` gates on ``COMMANDLINE`` — so meta stays authoritative
    # unless the user actually typed the flag.
    if _is_explicit(ctx, "insecure"):
        meta.browser.insecure = insecure
    if _is_explicit(ctx, "header") and header is not None:
        meta.browser.extra_headers = _parse_header_flags(header)
    if _is_explicit(ctx, "header_host"):
        meta.browser.header_host = header_host
    if _is_explicit(ctx, "format") and format:
        effective_format: str | None = format
    else:
        effective_format = meta.format

    # `--url` rewrites the first `goto` step's URL. Wins over any `--var URL=...`
    # substitution because it lands after `load()` has already substituted vars.
    # Only the first goto is touched — subsequent gotos are usually intra-app
    # navigation from the entry point, which the user still owns.
    steps = list(scenario.steps)
    if _is_explicit(ctx, "url") and url is not None:
        first_goto_idx = -1
        for i, s in enumerate(steps):
            if s.action == "goto":
                first_goto_idx = i
                break
        if first_goto_idx < 0:
            _die(
                "--url given but scenario has no `goto` step to rewrite; "
                "add a `- goto: ...` step or drop --url"
            )
        # `steps` items are pydantic BaseModels — `model_copy(update=...)` gives
        # us a new GotoStep with the overridden url and leaves the rest alone.
        steps[first_goto_idx] = steps[first_goto_idx].model_copy(update={"url": url})

    _run(
        lambda: _do_run(
            scenario=scenario.model_copy(update={"meta": meta, "steps": steps}),
            out=final_out,
            format_=effective_format,
            no_sidecar=no_sidecar,
            with_feedback=with_feedback,
            redact_patterns=compiled_redacts,
            strip_query_strings=strip_query_strings,
            emit_events=emit_events,
            grid=grid_cfg,
        )
    )


def _is_explicit(ctx: typer.Context, name: str) -> bool:
    """True if ``name`` was set explicitly on the command line.

    Typer 0.13+ vendors Click, so we can't `import click` for the
    ``ParameterSource`` enum. Compare on ``.name`` — stable across the
    Click versions Typer has shipped since 0.13.
    """
    try:
        source = ctx.get_parameter_source(name)
    except (AttributeError, LookupError):
        return False
    return getattr(source, "name", None) == "COMMANDLINE"


def _scenario_step_annotations(scenario: Any, result: Any) -> dict[int, StepAnnotation]:
    """Build per-recorder-step annotations from a completed scenario run.

    The recorder assigns ``step_index`` sequentially — every ``pre_action``
    call bumps it once. A step with ``repeat=3`` therefore produces 3
    distinct ``step_index`` values. Walk ``scenario.steps`` and
    ``result.results`` in lockstep with this bump so each frame's overlay
    label matches the action that produced it.
    """
    out: dict[int, StepAnnotation] = {}
    frame_step_index = 0
    result_index = 0
    for step in scenario.steps:
        for _ in range(step.repeat):
            if result_index >= len(result.results):
                # Scenario failed early — remaining steps never ran.
                return out
            r = result.results[result_index]
            label = step.label
            if not label:
                # Synthesize a label from the action + primary field.
                primary = (
                    getattr(step, "selector", None)
                    or getattr(step, "into", None)
                    or getattr(step, "url", None)
                    or ""
                )
                label = f"{step.action}: {primary[:40]}" if primary else step.action
            # Ripple only fires on click-shaped actions where the click landed.
            click_at = (
                r.cursor_xy if step.action in ("click", "dblclick") and r.status == "ok" else None
            )
            out[frame_step_index] = StepAnnotation(label=label, click_at=click_at)
            frame_step_index += 1
            result_index += 1
    return out


async def _do_run(
    *,
    scenario: Any,
    out: str,
    format_: str | None,
    no_sidecar: bool,
    with_feedback: bool = False,
    redact_patterns: list[re.Pattern[str]] | None = None,
    strip_query_strings: bool = False,
    emit_events: bool = False,
    grid: GridConfig | None = None,
) -> None:
    builder: ReportBuilder | None = None
    if not no_sidecar:
        vp = scenario.meta.viewport
        viewport_list: list[int] | None = None
        if vp:
            try:
                viewport_list = Viewport.parse(vp).as_list()
            except (TypeError, ValueError):
                viewport_list = None
        builder = ReportBuilder(engine=scenario.meta.engine, viewport=viewport_list)
        if grid is not None:
            builder.set_grid(grid)

    annotate_cfg = AnnotateConfig(grid=grid) if grid is not None else None
    with Recorder(fps=scenario.meta.fps, default_dwell=scenario.meta.dwell) as rec:
        result = await run_scenario(scenario, recorder=rec, builder=builder)
        rec.flush()
        # Overlays for scenario reels — same pipeline as `auto`. Every executed
        # step maps to one recorder step_index (repeat counts multiply); walk
        # them in parallel with `result.results` to build per-step annotations.
        annotate_frames_dir(
            rec.frames_dir,
            steps=_scenario_step_annotations(scenario, result),
            config=annotate_cfg,
        )
        out_path = Path(out)
        enc = encode(
            rec.frames_dir,
            out_path,
            fps=scenario.meta.fps,
            format=format_,  # type: ignore[arg-type]
        )
    if builder is not None and not result.ok:
        builder.add_warning(f"scenario failed at step {result.failed_at}")
    media = _make_media(enc, scenario.meta.fps)
    sidecar = _write_sidecar(
        out_path,
        no_sidecar,
        builder,
        media,
        with_feedback=with_feedback,
        redact_patterns=redact_patterns,
        strip_query_strings=strip_query_strings,
    )
    typer.echo(f"✔ {enc.path} ({enc.size_bytes // 1024} KB, {enc.frame_count} frames)")
    if not result.ok:
        failed_at = result.failed_at
        typer.secho(
            f"! scenario failed at step {failed_at}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        # #114: emit the augmented error (top-5 candidates + optional full
        # dump) to stderr so both humans and agents see the hint block,
        # not just the sidecar-consuming tooling.
        if failed_at is not None and 0 <= failed_at < len(result.results):
            err_text = result.results[failed_at].error
            if err_text:
                typer.secho(err_text, fg=typer.colors.RED, err=True)
    if sidecar:
        typer.echo(f"  sidecar: {sidecar}")
    # See #151 (AI-4): machine-readable summary line for JSONL parsers.
    # Off by default; on prints one JSON object after the prose summary
    # with the same key set the ``auto`` engine emits. ``pages`` and
    # ``clicks`` are counted from steps that actually EXECUTED successfully
    # (``result.results`` with ``status == "ok"``) — not from the parsed
    # YAML source — so a scenario that fails at step 3 of 5, or one whose
    # `optional` steps got skipped, reports the true executed count.
    # Mirrors the ``auto`` engine's semantics (see #172): both callers of
    # ``_emit_tour_complete`` now report executed pages/clicks.
    if emit_events:
        from clickcast.auto import _emit_tour_complete

        pages = sum(1 for r in result.results if r.status == "ok" and r.action == "goto")
        clicks = sum(
            1 for r in result.results if r.status == "ok" and r.action in ("click", "dblclick")
        )
        _emit_tour_complete(
            gif_path=str(enc.path),
            frames=enc.frame_count,
            duration_s=enc.duration_s,
            pages=pages,
            clicks=clicks,
            wall_s=enc.duration_s,
            sidecar_path=str(sidecar) if sidecar else None,
        )
    if not result.ok:
        raise typer.Exit(code=1)


# ==========================================================================
# clickcast shot
# ==========================================================================


@app.command(help="Capture a single screenshot.", epilog=_FEEDBACK_EPILOG)
def shot(
    url: Annotated[str, typer.Argument(help="Target URL.")],
    out: OutOpt = "shot.png",
    full_page: Annotated[
        bool, typer.Option("--full-page", help="Capture the full page, not just the viewport.")
    ] = False,
    wait: Annotated[
        str,
        typer.Option(
            "--wait",
            help="load | domcontentloaded | networkidle | selector | float seconds.",
        ),
    ] = "networkidle",
    viewport: ViewportArg = "1280x800",
    device: Device = None,
    engine: Engine = "chromium",
    headful: Headful = False,
    lang: Lang = None,
    dark: Dark = False,
    slowmo: Slowmo = 0,
    verbose: Verbose = 0,
    insecure: Insecure = False,
    header: Header = None,
    header_host: HeaderHost = None,
    grid: Grid = False,
    grid_pitch: GridPitch = 100,
    grid_color: GridColor = "#FFFFFF33",
    grid_style: GridStyle = "full",
) -> None:
    _setup_logging(verbose)
    grid_cfg = _build_grid_config(grid, grid_pitch, grid_color, grid_style)
    _run(
        lambda: _do_shot(
            url=url,
            out=out,
            full_page=full_page,
            wait=wait,
            session_kwargs=_session_kwargs(
                engine,
                viewport,
                device,
                headful,
                lang,
                dark,
                slowmo,
                insecure=insecure,
                extra_headers=_parse_header_flags(header),
                header_host=header_host,
            ),
            grid=grid_cfg,
        )
    )


async def _do_shot(
    *,
    url: str,
    out: str,
    full_page: bool,
    wait: str,
    session_kwargs: dict[str, Any],
    grid: GridConfig | None = None,
) -> None:
    async with Session(**session_kwargs) as sess:
        wait_value: str | float
        try:
            wait_value = float(wait)
        except ValueError:
            wait_value = wait
        await sess.goto(url, wait=wait_value)
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await sess.screenshot(path=out_path, full_page=full_page)
    # #171: apply the grid overlay to the saved screenshot. Kept as a
    # separate post-screenshot pass so the shot pipeline stays a single
    # file on disk — the annotator's frame-manifest machinery is overkill
    # for one image.
    if grid is not None:
        from PIL import Image

        from clickcast.annotate.grid import draw_grid

        with Image.open(out_path) as img:
            img.load()
            gridded = draw_grid(img.convert("RGBA"), grid)
        gridded.convert("RGB").save(out_path, format="PNG")
    typer.echo(f"✔ {out_path} ({out_path.stat().st_size // 1024} KB)")


# ==========================================================================
# clickcast init
# ==========================================================================


_STARTER_SCENARIO = """\
meta:
  name: {name}
  viewport: 1280x800
  fps: 12
  dwell: 1.0
  format: gif
  out: {out}

steps:
  - goto: {url}
    wait: networkidle
    label: Open site
  # add clicks / hovers / scrolls here
"""


@app.command(help="Scaffold a starter scenario file.", epilog=_FEEDBACK_EPILOG)
def init(
    path: Annotated[Path, typer.Argument(help="Output scenario path.")] = Path("tour.yml"),
    url: Annotated[
        str, typer.Option("--url", help="URL to seed the goto step with.")
    ] = "https://example.com",
    name: Annotated[str, typer.Option("--name", help="Human-readable scenario name.")] = "My tour",
    out: Annotated[
        str, typer.Option("--out", help="What the scenario's meta.out should point at.")
    ] = "reel.gif",
    from_auto: Annotated[
        bool,
        typer.Option(
            "--from-auto",
            help="Run auto-discovery on the URL once and seed steps from the results.",
        ),
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    if path.exists() and not force:
        _die(f"{path} already exists; pass --force to overwrite")

    if from_auto:
        content = _run(lambda: _scenario_from_discovery(url, name, out))
    else:
        content = _STARTER_SCENARIO.format(name=name, url=url, out=out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    typer.echo(f"✔ wrote {path}")


async def _scenario_from_discovery(url: str, name: str, out: str) -> str:
    async with Session(viewport=(1280, 800)) as sess:
        await sess.goto(url, wait="networkidle")
        elements = await discover(sess, limit=6)

    lines = [
        "meta:",
        f"  name: {name}",
        "  viewport: 1280x800",
        "  fps: 12",
        "  dwell: 1.0",
        "  format: gif",
        f"  out: {out}",
        "",
        "steps:",
        f"  - goto: {url}",
        "    wait: networkidle",
        "    label: Open site",
    ]
    for el in elements:
        selector = el.selector.replace('"', '\\"')
        label = el.text[:60] or el.role
        lines.append(f'  - click: "{selector}"')
        lines.append(f"    label: {label}")
        lines.append("    optional: true")
    return "\n".join(lines) + "\n"


# ==========================================================================
# clickcast elements
# ==========================================================================


@app.command(help="Dump interactive elements clickcast can see on a page.", epilog=_FEEDBACK_EPILOG)
def elements(
    url: Annotated[str, typer.Argument(help="Target URL.")],
    limit: Annotated[int, typer.Option("--limit", help="Cap on returned elements.")] = 20,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON on stdout.")
    ] = False,
    viewport: ViewportArg = "1280x800",
    device: Device = None,
    engine: Engine = "chromium",
    headful: Headful = False,
    lang: Lang = None,
    dark: Dark = False,
    slowmo: Slowmo = 0,
    verbose: Verbose = 0,
    insecure: Insecure = False,
    header: Header = None,
    header_host: HeaderHost = None,
    grid: Grid = False,
    grid_pitch: GridPitch = 100,
    grid_color: GridColor = "#FFFFFF33",
    grid_style: GridStyle = "full",
) -> None:
    _setup_logging(verbose)
    grid_cfg = _build_grid_config(grid, grid_pitch, grid_color, grid_style)
    result_elements, accessibility = _run(
        lambda: _do_elements(
            url=url,
            limit=limit,
            session_kwargs=_session_kwargs(
                engine,
                viewport,
                device,
                headful,
                lang,
                dark,
                slowmo,
                insecure=insecure,
                extra_headers=_parse_header_flags(header),
                header_host=header_host,
            ),
            grid=grid_cfg,
        )
    )
    if as_json:
        payload = []
        for e, a in zip(result_elements, accessibility, strict=True):
            d = e.to_dict()
            d["accessibility"] = a.to_dict()
            payload.append(d)
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for e, a in zip(result_elements, accessibility, strict=True):
        # #196: append the accessibility fusion (role/name/state/grid cell)
        # after the pre-existing heuristic line rather than replacing it —
        # the DOM-heuristic role/text keep driving selector authoring, the
        # accessibility fields are the additive "what Playwright itself
        # resolved" cross-check.
        a11y_bits = [f"role={a.role or '—'}", f"name={a.name or '—'}"]
        state_bits = [f"{k}={v}" for k, v in a.state.to_dict().items() if v is not None]
        if state_bits:
            a11y_bits.append(",".join(state_bits))
        if a.grid_cell is not None:
            a11y_bits.append(f"cell={a.grid_cell[0]},{a.grid_cell[1]}")
        typer.echo(
            f"  [{e.role:>10}] {(e.text or '<no name>')[:40]:<40}  {e.selector}  "
            f"(score={e.score})  a11y({' '.join(a11y_bits)})"
        )
    typer.echo(f"\n{len(result_elements)} elements")


async def _do_elements(
    *,
    url: str,
    limit: int,
    session_kwargs: dict[str, Any],
    grid: GridConfig | None = None,
) -> tuple[list[Element], list[AccessibleElement]]:
    async with Session(**session_kwargs) as sess:
        await sess.goto(url, wait="networkidle")
        found = await discover(sess, limit=limit)
        accessibility = await capture_accessibility_batch(sess, found, grid=grid)
        return found, accessibility


# ==========================================================================
# clickcast mcp  (#191)
# ==========================================================================


@app.command(
    "mcp",
    help="Start an MCP server exposing clickcast sessions for live agent control.",
    epilog=_FEEDBACK_EPILOG,
)
def mcp_cmd(
    viewport: ViewportArg = "1280x800",
    device: Device = None,
    engine: Engine = "chromium",
    headful: Headful = False,
    lang: Lang = None,
    dark: Dark = False,
    grid: Grid = False,
    grid_pitch: GridPitch = 100,
    grid_color: GridColor = "#FFFFFF33",
    grid_style: GridStyle = "full",
    verbose: Verbose = 0,
) -> None:
    """Run the stdio MCP server (see docs/mcp.md).

    Every browser-behaviour flag here is the DEFAULT for `start_session`
    when the connecting agent doesn't override it — the same "CLI flag as
    fallback" precedence every other clickcast command uses over `Config`
    (`CLICKCAST_*` env vars / TOML). Reads the same layered `Config` as
    `auto`/`run`; no new config surface.
    """
    _setup_logging(verbose)
    try:
        from clickcast.mcp import serve_stdio
    except ImportError as e:
        _die(
            "`clickcast mcp` requires the `mcp` extra — install with "
            f"`pip install 'clickcast[mcp]'` ({e})"
        )
        return
    grid_cfg = _build_grid_config(grid, grid_pitch, grid_color, grid_style)
    browser_opts = BrowserOpts(
        engine=engine,
        viewport=Viewport.parse(viewport),
        device=device,
        headful=headful,
        lang=lang,
        dark=dark,
    )
    serve_stdio(default_browser=browser_opts, default_grid=grid_cfg)


# ==========================================================================
# clickcast doctor
# ==========================================================================


@app.command(help="Diagnose the local environment.", epilog=_FEEDBACK_EPILOG)
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    cfg = load_config()
    report = _run_doctor_checks(configured_engine=cfg.engine)
    # #40 Track A: surface the feedback pointers in doctor output so a
    # stranded agent that only ran `clickcast doctor` still finds the loop.
    report["feedback"] = {
        "report_url": REPORT_URL,
        "schema_url": SCHEMA_URL,
        "docs_url": DOCS_URL,
        "diagnostics_command": DIAGNOSTICS_COMMAND,
    }
    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        for check in report["checks"]:
            marker = "✔" if check["ok"] else "✗"
            colour = typer.colors.GREEN if check["ok"] else typer.colors.RED
            typer.secho(f"  {marker} {check['name']}: {check['detail']}", fg=colour)
        typer.echo("")
        for line in feedback_pointer_lines():
            typer.secho(line, fg=typer.colors.BLUE)
    if not report["ok"]:
        raise typer.Exit(code=1)


def _run_doctor_checks(*, configured_engine: str = "chromium") -> dict[str, Any]:
    """Build the doctor report. `configured_engine` (the CLI/scenario/config
    default) is the only engine whose absence fails the overall `ok` — the
    other two are still checked and reported, but as informational (#225):
    a machine with only chromium installed shouldn't fail `doctor` over
    firefox/webkit it was never asked to use."""
    checks: list[dict[str, Any]] = []

    py_ok = sys.version_info >= (3, 10)
    checks.append(
        {
            "name": "python",
            "ok": py_ok,
            "detail": f"{sys.version.split()[0]} (need >= 3.10)",
        }
    )

    try:
        import playwright  # noqa: F401

        checks.append({"name": "playwright", "ok": True, "detail": "importable"})
    except ImportError as e:
        checks.append({"name": "playwright", "ok": False, "detail": f"import failed: {e}"})

    for engine_name in ("chromium", "firefox", "webkit"):
        found = _find_playwright_engine(engine_name)
        if found is None:
            detail = "not installed (run `clickcast install`)"
        else:
            path, kind = found
            detail = f"{kind}: {path}"
        checks.append(
            {
                "name": f"engine.{engine_name}",
                "ok": found is not None,
                "detail": detail,
                "required": engine_name == configured_engine,
            }
        )

    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        checks.append({"name": "ffmpeg", "ok": bool(ffmpeg), "detail": ffmpeg})
    except Exception as e:  # pragma: no cover — imageio_ffmpeg is a hard dep
        checks.append({"name": "ffmpeg", "ok": False, "detail": str(e)})

    config_path = Path(user_config_dir(_APP_NAME)) / "config.toml"
    checks.append(
        {
            "name": "config-dir",
            "ok": config_path.parent.exists() or True,  # non-existence is fine
            "detail": str(config_path),
        }
    )

    ok = all(c["ok"] for c in checks if c.get("required", True))
    return {"ok": ok, "checks": checks}


# Moved to core/engines.py (shared with Session's pre-flight check) — kept
# importable under its original name since tests/test_cli.py references it.
_find_playwright_engine = find_installed_engine


# ==========================================================================
# clickcast config …  (#177)
# ==========================================================================

# Sub-app: `clickcast config path|list|get|set`. Grouped rather than a
# single command with a string-dispatched action arg so each subcommand
# gets its own --help, shell completion sees the valid names, and
# arg-requirement checks fall out of the Typer signature. See
# `src/clickcast/config/cli.py` for the command bodies. The list-value
# formatter that #175 added lives there too, next to its `list_cmd` caller.
app.add_typer(config_app, name="config")


# ==========================================================================
# clickcast install
# ==========================================================================


@app.command(help="Install browser engines (wraps `playwright install`).", epilog=_FEEDBACK_EPILOG)
def install(
    engines: Annotated[
        list[str] | None,
        typer.Argument(help="Engines to install (default: chromium)."),
    ] = None,
    with_deps: Annotated[
        bool,
        typer.Option("--with-deps", help="Also install system libraries (needs sudo on Linux)."),
    ] = False,
) -> None:
    engine_list = engines or ["chromium"]
    code = _install_engine(engine_list, with_deps=with_deps)
    raise typer.Exit(code=code)


# ==========================================================================
# clickcast report-bug  (#40 Track B)
# ==========================================================================


@app.command(
    "report-bug",
    help="Turn a sidecar into an actionable AI-agent bug report.",
    epilog=_FEEDBACK_EPILOG,
)
def report_bug(
    sidecar_path: Annotated[
        Path,
        typer.Argument(help="Path to a `reel.gif.json` sidecar to inspect."),
    ],
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit the Track-C payload (see docs/agent-report-schema/v1.json) instead of prose.",
        ),
    ] = False,
    open_url: Annotated[
        bool,
        typer.Option(
            "--open",
            help="Also launch the prefilled GitHub issue URL via the OS opener.",
        ),
    ] = False,
    redact: Annotated[
        bool,
        typer.Option(
            "--redact/--no-redact",
            help=(
                "Sanitize URLs, selectors, and visible text in the sidecar excerpt. "
                "Default on — safe to share. Turn off only for open-source public targets."
            ),
        ),
    ] = True,
    note: Annotated[
        str | None,
        typer.Option(
            "--note",
            help="Free-text environment note (e.g. `behind corporate proxy; TLS interception on`).",
        ),
    ] = None,
) -> None:
    from clickcast.feedback import load as load_report
    from clickcast.feedback.report_bug import (
        _open_url,
        build_agent_report,
        prefilled_issue_url,
        render_diagnostics,
    )

    if not sidecar_path.exists():
        _die(f"sidecar not found: {sidecar_path}")

    try:
        report = load_report(sidecar_path)
    except Exception as e:
        _die(f"could not load sidecar {sidecar_path}: {e}")

    payload = build_agent_report(report, redact=redact, environment_note=note)
    url = prefilled_issue_url(payload)

    if as_json:
        payload["issue_url"] = url
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(render_diagnostics(payload))
        typer.echo("")
        typer.secho("Open this URL to file (title + body prefilled):", fg=typer.colors.BLUE)
        typer.echo(url)

    if open_url:
        _open_url(url)


# ==========================================================================
# clickcast assertions  (#112)
# ==========================================================================


@app.command(
    "assertions",
    help="Distill a sidecar to its CI-stable assertion set (optionally diff a baseline).",
    epilog=_FEEDBACK_EPILOG,
)
def assertions(
    sidecar_path: Annotated[
        Path,
        typer.Argument(help="Path to a `reel.gif.json` sidecar to distill."),
    ],
    baseline: Annotated[
        Path | None,
        typer.Option(
            "--baseline",
            help=(
                "Committed baseline JSON produced by a previous `--json` run. "
                "When set, diff current vs baseline and exit non-zero on drift."
            ),
        ),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit the distilled assertion set as JSON on stdout (see docs/assertions-schema/v1.json).",
        ),
    ] = False,
) -> None:
    from clickcast.feedback import load as load_report
    from clickcast.feedback.assertions import (
        build_assertions,
        diff_assertions,
        load_assertions,
    )

    if not sidecar_path.exists():
        _die(f"sidecar not found: {sidecar_path}")

    try:
        report = load_report(sidecar_path)
    except Exception as e:
        _die(f"could not load sidecar {sidecar_path}: {e}")

    current = build_assertions(report)

    if baseline is None:
        # No baseline supplied — just emit the distillation. Human-readable
        # form falls back to compact JSON so the output is always machine-
        # tailable (there is no interesting prose form for an assertion set).
        typer.echo(json.dumps(current, indent=2))
        return

    if not baseline.exists():
        _die(f"baseline not found: {baseline}")

    try:
        baseline_data = load_assertions(baseline)
    except Exception as e:
        _die(f"could not load baseline {baseline}: {e}")

    drift, is_clean = diff_assertions(current, baseline_data)

    if as_json:
        typer.echo(
            json.dumps(
                {"is_clean": is_clean, "drift": drift, "current": current},
                indent=2,
            )
        )
    else:
        if is_clean:
            typer.secho("✔ assertions match baseline (no drift)", fg=typer.colors.GREEN)
        else:
            typer.secho(
                f"✗ {len(drift)} drift entr{'y' if len(drift) == 1 else 'ies'}:",
                fg=typer.colors.RED,
            )
            for line in drift:
                typer.secho(f"  - {line}", fg=typer.colors.RED)

    if not is_clean:
        raise typer.Exit(code=1)


# ==========================================================================
# clickcast diff  (#201/#204) — pixel-level visual diff, `assertions`' companion
# ==========================================================================


@app.command(
    "diff",
    help="Pixel-diff a run's frames against a baseline's (companion to `assertions`).",
    epilog=_FEEDBACK_EPILOG,
)
def diff(
    run_sidecar: Annotated[
        Path,
        typer.Argument(help="Path to the current run's `reel.gif.json` sidecar."),
    ],
    baseline_sidecar: Annotated[
        Path,
        typer.Argument(help="Path to the baseline run's `reel.gif.json` sidecar."),
    ],
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help=(
                "Directory for region-highlighted diff images + summary.json. "
                "Default: `<run-sidecar-stem>.diff/` next to the run sidecar."
            ),
        ),
    ] = None,
    threshold: Annotated[
        float,
        typer.Option(
            "--threshold",
            help=(
                "Per-pixel channel-delta cutoff (0-255 scale) above which a pixel "
                "counts as changed — the anti-aliasing/noise floor, not a percent."
            ),
        ),
    ] = DEFAULT_VISUAL_DIFF_THRESHOLD,
    no_exclude_overlays: Annotated[
        bool,
        typer.Option(
            "--no-exclude-overlays",
            help=(
                "Disable exclusion of clickcast's own annotator overlays (progress "
                "bar / label / actions panel / cursor+ripple) — strict raw-pixel diff."
            ),
        ),
    ] = False,
    fail_above: Annotated[
        float | None,
        typer.Option(
            "--fail-above",
            help=(
                "Exit non-zero when any step's changed_pct exceeds this percentage "
                "(0-100), or when a step could not be paired with its counterpart. "
                "Omit to report only (always exits 0) — usable standalone as a CI "
                "gate, not only alongside `assertions`."
            ),
        ),
    ] = None,
) -> None:
    from clickcast.feedback.visual_diff import max_changed_pct
    from clickcast.feedback.visual_diff import visual_diff as _visual_diff

    if not run_sidecar.exists():
        _die(f"sidecar not found: {run_sidecar}")
    if not baseline_sidecar.exists():
        _die(f"baseline not found: {baseline_sidecar}")

    try:
        report = _visual_diff(
            run_sidecar,
            baseline_sidecar,
            threshold=threshold,
            out_dir=out,
            exclude_overlays=not no_exclude_overlays,
        )
    except Exception as e:
        _die(f"could not compute visual diff: {e}")

    worst = max_changed_pct(report)
    for step in report.steps:
        marker = "✗" if fail_above is not None and step.changed_pct > fail_above else "·"
        color = typer.colors.RED if marker == "✗" else typer.colors.GREEN
        region_note = f", {len(step.regions)} region(s)" if step.regions else ""
        typer.secho(
            f"{marker} step {step.run_index} ({step.label or step.run_index}): "
            f"{step.changed_pct:.2f}% changed{region_note}",
            fg=color,
        )
    for u in report.unmatched_steps:
        typer.secho(
            f"! {u.side} step {u.index} ({u.label or u.index}): unmatched — {u.reason}",
            fg=typer.colors.YELLOW,
        )

    typer.echo(f"worst step: {worst:.2f}% changed; wrote report to {_visual_diff_out_hint(report)}")

    if fail_above is None:
        return

    should_fail = worst > fail_above or bool(report.unmatched_steps)
    if should_fail:
        raise typer.Exit(code=1)


def _visual_diff_out_hint(report: VisualDiffReport) -> str:
    for step in report.steps:
        if step.diff_image_path:
            return str(Path(step.diff_image_path).parent)
    return "(no diff images written — see summary.json in --out)"


# ==========================================================================
# clickcast skill  (#103)
# ==========================================================================


@app.command(
    "skill",
    help="Print an AI-friendly self-introduction covering every clickcast command.",
    epilog=_FEEDBACK_EPILOG,
)
def skill(
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Emit as JSON matching docs/skill-schema/v1.json.",
        ),
    ] = False,
) -> None:
    from clickcast.skill import build_payload, render_markdown

    if as_json:
        typer.echo(json.dumps(build_payload(), indent=2))
    else:
        typer.echo(render_markdown())


# ==========================================================================
# clickcast feedback …  (#124 v1)
# ==========================================================================

# Sub-app: `clickcast feedback start|stop|status|list|summary`. Grouped
# rather than flat so the noun/verb pairing reads naturally. See
# `src/clickcast/feedback/session/cli.py` for the command bodies.
app.add_typer(feedback_app, name="feedback")


# #151 REF-4: freeze the command → Config-key introspection ONCE, now that
# every `@app.command` decorator above has registered its callback with the
# Typer app. `_config_default_map` reads this table on every invocation
# instead of re-walking `app.registered_commands` + `inspect.signature`.
_CLI_COMMAND_PARAMS.update(_build_cli_command_params())


# ==========================================================================
# Entrypoint wrapper — #124 recording hook
# ==========================================================================


def main() -> None:
    """Console-script entrypoint. Wraps :data:`app` so an active feedback
    session (see #124) gets one JSONL line per invocation with the real
    exit code and wall time.

    Click's ``Context.call_on_close`` runs before the ``SystemExit``
    surfaces to callers — meaning it can't see the code the command
    exited with. Wrapping at the entrypoint layer is the simplest
    place to observe both success (code 0) and failure (whatever
    ``typer.Exit`` / ``SystemExit`` was raised). The recording is
    best-effort: if anything goes wrong appending the event, the
    original exit still propagates unchanged so users NEVER get a
    mystery failure caused by the recorder.
    """
    start_monotonic = time.monotonic()
    argv = list(sys.argv[1:])
    exit_code = 0
    # #174: only in CLI mode do we touch root logging — attach a stderr
    # handler if the process doesn't have one already. Library callers that
    # invoke Typer commands directly (or `_setup_logging`) never reach this.
    _ensure_cli_root_handler()
    try:
        app()
    except SystemExit as e:
        # `typer.Exit(code=N)` bubbles up as `SystemExit(N)`. Preserve it.
        code = e.code
        if isinstance(code, int):
            exit_code = code
        elif code is None:
            exit_code = 0
        else:
            # Non-numeric exit (a message string) — treat as error.
            exit_code = 1
        raise
    except BaseException:
        # Any uncaught exception is a nonzero exit — record it as such
        # before re-raising so the user still sees the traceback.
        exit_code = 1
        raise
    finally:
        wall_ms = int((time.monotonic() - start_monotonic) * 1000)
        record_invocation_safe(
            argv,
            exit_code=exit_code,
            wall_time_ms=wall_ms,
        )


if __name__ == "__main__":
    main()
