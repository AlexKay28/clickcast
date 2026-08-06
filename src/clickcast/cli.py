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
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import typer
from platformdirs import user_config_dir

from clickcast import __version__
from clickcast.annotate import AnnotateConfig, StepAnnotation, annotate_frames_dir
from clickcast.auto import AutoConfig, run_tour
from clickcast.capture import Recorder
from clickcast.config import (
    Config as ConfigModel,
)
from clickcast.config import (
    get_effective_value,
    set_user_value,
    user_config_path,
)
from clickcast.config import (
    load as load_config,
)
from clickcast.core.actions import set_dump_elements
from clickcast.core.opts import BrowserOpts
from clickcast.core.session import Session
from clickcast.core.viewport import Viewport
from clickcast.discovery import Element, discover
from clickcast.encode import encode
from clickcast.feedback import Media, ReportBuilder, feedback_pointer_lines
from clickcast.feedback import write as write_report
from clickcast.feedback.pointers import (
    DIAGNOSTICS_COMMAND,
    DOCS_URL,
    REPORT_URL,
    SCHEMA_URL,
    SKILL_URL,
)
from clickcast.feedback.session.cli import feedback_app
from clickcast.feedback.session.storage import record_invocation_safe
from clickcast.scenario import ScenarioError, load
from clickcast.scenario import run as run_scenario

_APP_NAME = "clickcast"

log = logging.getLogger("clickcast.auto")


def _setup_logging(verbose: int) -> None:
    """Configure root logging for the current command based on -v count.

    0 → WARNING (default), 1 → INFO (per-click + per-page traces),
    2+ → DEBUG (per-frame + internal wait details).
    """
    if verbose <= 0:
        level = logging.WARNING
    elif verbose == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG
    # `force=True` so a second CLI invocation in the same process (tests) can
    # re-configure without leftover handlers doubling every line.
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )


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
) -> None:
    _setup_logging(verbose)
    set_dump_elements(dump_elements)
    if traversal not in ("dfs", "bfs"):
        _die(f"--traversal must be 'dfs' or 'bfs', got {traversal!r}")
    if pace not in _PACE_TABLE:
        _die(f"--pace must be one of {sorted(_PACE_TABLE)}, got {pace!r}")
    compiled_redacts = _compile_redact_patterns(redact_pattern)
    extra_headers = _parse_header_flags(header)

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

    asyncio.run(
        _do_auto(
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
) -> None:
    # The AutoConfig.target_highlight flag drives recorder-side padding +
    # bbox lookup; the annotator itself needs its own toggle so the layer
    # actually renders. Keep the two in lockstep here so a shipped caller
    # that flips one always gets the other.
    annotate = AnnotateConfig(target_highlight=target_highlight)
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
) -> None:
    set_dump_elements(dump_elements)
    compiled_redacts = _compile_redact_patterns(redact_pattern)
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

    asyncio.run(
        _do_run(
            scenario=scenario.model_copy(update={"meta": meta, "steps": steps}),
            out=final_out,
            format_=effective_format,
            no_sidecar=no_sidecar,
            with_feedback=with_feedback,
            redact_patterns=compiled_redacts,
            strip_query_strings=strip_query_strings,
            emit_events=emit_events,
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

    with Recorder(fps=scenario.meta.fps, default_dwell=scenario.meta.dwell) as rec:
        result = await run_scenario(scenario, recorder=rec, builder=builder)
        rec.flush()
        # Overlays for scenario reels — same pipeline as `auto`. Every executed
        # step maps to one recorder step_index (repeat counts multiply); walk
        # them in parallel with `result.results` to build per-step annotations.
        annotate_frames_dir(rec.frames_dir, steps=_scenario_step_annotations(scenario, result))
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
    dark: Dark = False,
    insecure: Insecure = False,
    header: Header = None,
    header_host: HeaderHost = None,
) -> None:
    asyncio.run(
        _do_shot(
            url=url,
            out=out,
            full_page=full_page,
            wait=wait,
            session_kwargs=_session_kwargs(
                engine,
                viewport,
                device,
                False,
                None,
                dark,
                insecure=insecure,
                extra_headers=_parse_header_flags(header),
                header_host=header_host,
            ),
        )
    )


async def _do_shot(
    *,
    url: str,
    out: str,
    full_page: bool,
    wait: str,
    session_kwargs: dict[str, Any],
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
        content = asyncio.run(_scenario_from_discovery(url, name, out))
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
    engine: Engine = "chromium",
    insecure: Insecure = False,
    header: Header = None,
    header_host: HeaderHost = None,
) -> None:
    result_elements = asyncio.run(
        _do_elements(
            url=url,
            limit=limit,
            session_kwargs=_session_kwargs(
                engine,
                viewport,
                None,
                False,
                None,
                False,
                insecure=insecure,
                extra_headers=_parse_header_flags(header),
                header_host=header_host,
            ),
        )
    )
    if as_json:
        typer.echo(json.dumps([e.to_dict() for e in result_elements], indent=2, ensure_ascii=False))
        return
    for e in result_elements:
        typer.echo(
            f"  [{e.role:>10}] {(e.text or '<no name>')[:40]:<40}  {e.selector}  (score={e.score})"
        )
    typer.echo(f"\n{len(result_elements)} elements")


async def _do_elements(*, url: str, limit: int, session_kwargs: dict[str, Any]) -> list[Element]:
    async with Session(**session_kwargs) as sess:
        await sess.goto(url, wait="networkidle")
        return await discover(sess, limit=limit)


# ==========================================================================
# clickcast doctor
# ==========================================================================


@app.command(help="Diagnose the local environment.", epilog=_FEEDBACK_EPILOG)
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    report = _run_doctor_checks()
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


def _run_doctor_checks() -> dict[str, Any]:
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
        path = _find_playwright_engine(engine_name)
        checks.append(
            {
                "name": f"engine.{engine_name}",
                "ok": path is not None,
                "detail": str(path) if path else "not installed (run `clickcast install`)",
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

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks}


def _find_playwright_engine(engine: str) -> Path | None:
    """Return the resolved executable path for a Playwright browser, or None."""
    cache_root = Path.home() / ".cache" / "ms-playwright"
    if not cache_root.exists():
        alt = Path.home() / "Library" / "Caches" / "ms-playwright"
        cache_root = alt if alt.exists() else cache_root
    if not cache_root.exists():
        return None
    prefix = {"chromium": "chromium", "firefox": "firefox", "webkit": "webkit"}.get(engine)
    if not prefix:
        return None
    matches = sorted(cache_root.glob(f"{prefix}*"))
    return matches[-1] if matches else None


# ==========================================================================
# clickcast config
# ==========================================================================


@app.command(help="Read / write persistent defaults.", epilog=_FEEDBACK_EPILOG)
def config(
    action: Annotated[str, typer.Argument(help="path | get | set | list")],
    key: Annotated[str | None, typer.Argument(help="Config key (for get / set).")] = None,
    value: Annotated[str | None, typer.Argument(help="Value (for set).")] = None,
) -> None:
    if action == "path":
        typer.echo(str(user_config_path()))
        return
    if action == "list":
        for k in sorted(ConfigModel.model_fields):
            typer.echo(f"  {k:<12}  {get_effective_value(k)}")
        return
    if action == "get":
        if not key:
            raise typer.BadParameter("`config get` requires a key")
        try:
            typer.echo(get_effective_value(key))
        except KeyError as e:
            _die(str(e))
        return
    if action == "set":
        if not key or value is None:
            raise typer.BadParameter("`config set` requires both a key and a value")
        try:
            written_to = set_user_value(key, value)
        except (KeyError, ValueError) as e:
            _die(str(e))
        typer.echo(f"✔ {key} = {value}  ({written_to})")
        return
    raise typer.BadParameter(f"unknown action {action!r}; expected path | get | set | list")


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
    playwright_bin = shutil.which("playwright") or f"{sys.executable} -m playwright"
    cmd = [*playwright_bin.split(), "install"]
    if with_deps:
        cmd.append("--with-deps")
    cmd.extend(engine_list)
    typer.echo(f"→ {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    raise typer.Exit(code=result.returncode)


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
