"""Clickcast CLI — Typer app wiring every command promised in the README.

Command modules stay thin — each dispatches into `clickcast.core`,
`clickcast.scenario`, `clickcast.discovery`, or `clickcast.encode`. Business
logic lives in those subsystems, not here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any

import typer
from platformdirs import user_config_dir

from clickcast import __version__
from clickcast.annotate import StepAnnotation, annotate_frames_dir
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
from clickcast.core.actions import ClickStep, GotoStep, ScrollStep, execute
from clickcast.core.session import Session
from clickcast.discovery import Element, discover
from clickcast.discovery.urlutil import is_same_origin, normalize_url
from clickcast.encode import encode
from clickcast.feedback import Media, ReportBuilder
from clickcast.feedback import write as write_report
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


# ==========================================================================
# Shared option types (Annotated makes them reusable across commands)
# ==========================================================================

Viewport = Annotated[str, typer.Option("--viewport", help="Viewport WxH, e.g. 1280x800.")]
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
Fps = Annotated[int, typer.Option("--fps", help="Frames per second.")]
Verbose = Annotated[
    int,
    typer.Option("--verbose", "-v", count=True, help="Increase output verbosity."),
]


# ==========================================================================
# Helpers
# ==========================================================================


def _parse_viewport(v: str) -> tuple[int, int]:
    try:
        w, h = v.lower().split("x", 1)
        return (int(w), int(h))
    except ValueError as e:
        raise typer.BadParameter(f"invalid viewport {v!r}; expected WxH") from e


def _die(msg: str, code: int = 1) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


def _session_kwargs(
    engine: str,
    viewport: str,
    device: str | None,
    headful: bool,
    lang: str | None,
    dark: bool,
    slowmo: int = 0,
) -> dict[str, Any]:
    return {
        "engine": engine,
        "viewport": _parse_viewport(viewport),
        "device": device,
        "headful": headful,
        "lang": lang,
        "dark": dark,
        "slowmo": slowmo,
    }


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
) -> Path | None:
    if no_sidecar or builder is None:
        return None
    sidecar = out.with_suffix(out.suffix + ".json")
    report = builder.build(media)
    write_report(report, sidecar)
    return sidecar


# ==========================================================================
# Top-level
# ==========================================================================


# Which Config fields are consumed by which subcommand. Keeping the mapping
# explicit rather than reflecting on option lists — Click's `default_map`
# errors on unknown keys, so a typo here would fail loudly.
_CONFIG_KEYS_PER_COMMAND: dict[str, tuple[str, ...]] = {
    "auto": (
        "viewport",
        "device",
        "engine",
        "headful",
        "lang",
        "dark",
        "fps",
        "dwell",
        "format",
        "quality",
        "loop",
    ),
    "run": ("format", "headful", "slowmo"),
    "shot": ("viewport", "device", "engine", "dark"),
    "elements": ("viewport", "engine"),
}


def _config_default_map() -> dict[str, dict[str, Any]]:
    """Build Click's per-command `default_map` from the layered Config.

    Load-once per invocation: env vars + project TOML + user TOML resolved
    now, then Click uses these as fallbacks unless an explicit CLI flag wins.
    """
    try:
        cfg = load_config()
    except Exception:
        return {}
    fields = cfg.model_dump()
    return {
        cmd: {k: fields[k] for k in keys if k in fields}
        for cmd, keys in _CONFIG_KEYS_PER_COMMAND.items()
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


@app.command(help="Auto-discover interactive elements and record a tour.")
def auto(
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
    viewport: Viewport = "1280x800",
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
    verbose: Verbose = 0,
) -> None:
    _setup_logging(verbose)
    asyncio.run(
        _do_auto(
            url=url,
            out=out,
            max_steps=max_steps,
            max_pages=max_pages,
            dwell=dwell,
            initial_wait=initial_wait,
            session_kwargs=_session_kwargs(engine, viewport, device, headful, lang, dark),
            fps=fps,
            format_=format,
            quality=quality,
            loop=loop,
            no_sidecar=no_sidecar,
        )
    )


async def _explore_page(
    *,
    sess: Session,
    rec: Recorder,
    builder: ReportBuilder | None,
    url: str,
    dwell: float,
    initial_wait: float,
    click_budget: int,
    step_index: int,
    step_annotations: dict[int, StepAnnotation],
    page_label: str,
) -> tuple[int, int, list[str]]:
    """Goto ``url``, discover, click up to ``click_budget`` elements, scroll.

    Returns ``(next_step_index, clicks_used, discovered_urls)``. ``clicks_used``
    lets the caller decrement its global budget; ``discovered_urls`` are the
    same-origin destinations noticed while clicking (dedup happens in the
    caller).
    """
    discovered_urls: list[str] = []
    page_started = time.monotonic()
    log.info("%s → open %s", page_label, url)

    goto = GotoStep(url=url, wait="networkidle", dwell=dwell)
    await rec.pre_action(sess)
    result = await execute(goto, sess)
    if not result.ok:
        typer.secho(f"  skipped {url}: {result.error}", fg=typer.colors.YELLOW, err=True)
        log.warning("%s · skipped: %s", page_label, result.error)
        return step_index, 0, discovered_urls
    if initial_wait > 0:
        log.debug("%s · held %.1fs after networkidle for hydration", page_label, initial_wait)
        await sess.wait(initial_wait)
    frames_goto = await rec.post_action(sess, result, goto)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · open")
    if builder:
        await builder.record_step(index=step_index, step=goto, result=result, frames=frames_goto)
    step_index += 1

    # Discover a generous pool so we don't starve when max_steps is small;
    # the click budget still caps how many we actually invoke.
    elements = await discover(sess, limit=max(click_budget * 2, 20))
    log.info(
        "%s · discovered %d elements, click budget: %d", page_label, len(elements), click_budget
    )
    if builder and step_index == 1:
        builder.set_discovered(elements[:click_budget])

    clicked = 0
    for element in elements:
        if clicked >= click_budget:
            break
        step = ClickStep(
            selector=element.selector,
            dwell=dwell,
            optional=True,
            label=element.text[:60] or element.role,
        )
        url_before = sess.page.url
        log.info(
            "%s · click %d/%d · %s:%s",
            page_label,
            clicked + 1,
            click_budget,
            element.role,
            (element.text[:40] or "").strip() or element.selector,
        )
        await rec.pre_action(sess)
        r = await execute(step, sess)
        frames_step = await rec.post_action(sess, r, step)
        step_annotations[step_index] = StepAnnotation(
            label=f"{page_label} · click · {step.label}" if step.label else f"{page_label} · click",
            click_at=r.cursor_xy if r.status == "ok" else None,
        )
        if r.status == "ok":
            clicked += 1
        else:
            log.warning("%s · click failed: %s", page_label, r.error)
        if builder:
            await builder.record_step(index=step_index, step=step, result=r, frames=frames_step)
        step_index += 1

        # Post-click: did we navigate? Note the destination and try to restore
        # the page so we can keep clicking the remaining discovered elements.
        # Same-origin nav: page.go_back() and continue. Cross-origin nav:
        # bail (we shouldn't drive further on someone else's site).
        #
        # `wait_until="domcontentloaded"` + 5s hard timeout: networkidle can
        # hang forever on sites with WebSockets / SSE / dev-server HMR (react.dev
        # was our smoking gun — the fix in #56 made the demo run 30+ minutes).
        # DOM-ready is enough since we're just going back to re-select elements.
        url_after = sess.page.url
        if url_after != url_before:
            discovered_urls.append(url_after)
            if not is_same_origin(url_after, url_before):
                log.info("%s · nav to cross-origin %s → bailing", page_label, url_after)
                break
            log.info("%s · nav to %s → going back", page_label, url_after)
            back_started = time.monotonic()
            try:
                await sess.page.go_back(wait_until="domcontentloaded", timeout=5000)
            except Exception as e:
                # Some sites (chained redirects, popstate handlers) refuse
                # go_back cleanly. Give up on further clicks on this page.
                log.warning("%s · go_back failed (%s) → stopping page", page_label, e)
                break
            # Verify we actually returned to the original page — some sites
            # replace history so go_back lands somewhere else.
            if sess.page.url != url_before:
                log.warning(
                    "%s · go_back landed at %s (expected %s) → stopping page",
                    page_label,
                    sess.page.url,
                    url_before,
                )
                break
            log.debug("%s · go_back OK in %.2fs", page_label, time.monotonic() - back_started)
        await sess.wait(0.3)

    scroll = ScrollStep(by=600, dwell=dwell)
    log.info("%s · scroll +600px", page_label)
    await rec.pre_action(sess)
    r = await execute(scroll, sess)
    frames_scroll = await rec.post_action(sess, r, scroll)
    step_annotations[step_index] = StepAnnotation(label=f"{page_label} · scroll")
    if builder:
        await builder.record_step(index=step_index, step=scroll, result=r, frames=frames_scroll)
    step_index += 1

    log.info(
        "%s · done in %.1fs (%d clicks used, %d nav candidates)",
        page_label,
        time.monotonic() - page_started,
        clicked,
        len(discovered_urls),
    )
    return step_index, clicked, discovered_urls


async def _do_auto(
    *,
    url: str,
    out: str,
    max_steps: int,
    max_pages: int,
    dwell: float,
    initial_wait: float,
    session_kwargs: dict[str, Any],
    fps: int,
    format_: str | None,
    quality: int,
    loop: int,
    no_sidecar: bool,
) -> None:
    if max_pages < 1:
        _die("--max-pages must be >= 1")

    tour_started = time.monotonic()
    log.info(
        "starting auto tour: url=%s max_pages=%d max_steps=%d dwell=%.2fs fps=%d",
        url,
        max_pages,
        max_steps,
        dwell,
        fps,
    )

    async with Session(**session_kwargs) as sess:
        builder: ReportBuilder | None = None
        if not no_sidecar:
            builder = ReportBuilder(
                url=url,
                engine=session_kwargs.get("engine", "chromium"),
                viewport=session_kwargs.get("viewport"),
            )
            builder.attach(sess)

        with Recorder(fps=fps, default_dwell=dwell) as rec:
            step_annotations: dict[int, StepAnnotation] = {}
            step_index = 0
            visited: set[str] = set()
            queue: deque[str] = deque([url])
            pages_visited = 0
            clicks_remaining = max_steps

            while queue and pages_visited < max_pages and clicks_remaining > 0:
                current = queue.popleft()
                key = normalize_url(current)
                if key in visited:
                    log.debug("skipping already-visited %s", current)
                    continue
                visited.add(key)
                pages_visited += 1
                page_label = f"page {pages_visited}/{max_pages}"

                step_index, clicks_used, discovered = await _explore_page(
                    sess=sess,
                    rec=rec,
                    builder=builder,
                    url=current,
                    dwell=dwell,
                    initial_wait=initial_wait,
                    click_budget=clicks_remaining,
                    step_index=step_index,
                    step_annotations=step_annotations,
                    page_label=page_label,
                )
                clicks_remaining -= clicks_used

                # First page must have discovered elements; downstream pages
                # can be scroll-only (a legitimate destination).
                if pages_visited == 1 and step_index == 1:
                    _die("no interactive elements discovered on start page")

                new_enqueued = 0
                for candidate in discovered:
                    if not is_same_origin(candidate, url):
                        continue
                    if normalize_url(candidate) in visited:
                        continue
                    queue.append(candidate)
                    new_enqueued += 1
                log.info(
                    "%s · budget: %d clicks left, queue: %d urls (+%d new)",
                    page_label,
                    clicks_remaining,
                    len(queue),
                    new_enqueued,
                )

            log.info("BFS done. Flushing %d step annotations...", len(step_annotations))
            rec.flush()
            log.info("annotating frames...")
            annotate_frames_dir(rec.frames_dir, steps=step_annotations)
            log.info("encoding %s...", out)
            out_path = Path(out)
            enc = encode(
                rec.frames_dir,
                out_path,
                fps=fps,
                quality=quality,
                loop=loop,
                format=format_,  # type: ignore[arg-type]
            )
            media = _make_media(enc, fps)
            sidecar = _write_sidecar(out_path, no_sidecar, builder, media)

    tour_elapsed = time.monotonic() - tour_started
    typer.echo(
        f"✔ {enc.path} ({enc.size_bytes // 1024} KB, {enc.frame_count} frames, "
        f"{enc.duration_s:.1f}s reel, {pages_visited} page(s), "
        f"{max_steps - clicks_remaining} clicks, wall {tour_elapsed:.1f}s)"
    )
    if sidecar:
        typer.echo(f"  sidecar: {sidecar}")


# ==========================================================================
# clickcast run
# ==========================================================================


@app.command(help="Run a YAML scenario end-to-end.")
def run(
    ctx: typer.Context,
    scenario_path: Annotated[Path, typer.Argument(help="Path to a scenario file.")],
    out: Annotated[
        str | None, typer.Option("--out", "-o", help="Override scenario meta.out.")
    ] = None,
    format: FormatOpt = None,
    headful: Headful = False,
    slowmo: Slowmo = 0,
    var: Annotated[
        list[str] | None,
        typer.Option("--var", help="Inject a scenario variable as key=value."),
    ] = None,
    no_sidecar: NoSidecar = False,
    verbose: Verbose = 0,
) -> None:
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
    meta = scenario.meta.model_copy()
    final_out = out or meta.out
    if _is_explicit(ctx, "headful"):
        meta.headful = headful
    if _is_explicit(ctx, "slowmo"):
        meta.slowmo = slowmo
    if _is_explicit(ctx, "format") and format:
        effective_format: str | None = format
    else:
        effective_format = meta.format

    asyncio.run(
        _do_run(
            scenario=scenario.model_copy(update={"meta": meta}),
            out=final_out,
            format_=effective_format,
            no_sidecar=no_sidecar,
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


async def _do_run(
    *,
    scenario: Any,
    out: str,
    format_: str | None,
    no_sidecar: bool,
) -> None:
    builder: ReportBuilder | None = None
    if not no_sidecar:
        vp = scenario.meta.viewport
        viewport_list: list[int] | None = None
        if vp:
            try:
                w, h = vp.lower().split("x", 1)
                viewport_list = [int(w), int(h)]
            except ValueError:
                viewport_list = None
        builder = ReportBuilder(engine=scenario.meta.engine, viewport=viewport_list)

    with Recorder(fps=scenario.meta.fps, default_dwell=scenario.meta.dwell) as rec:
        result = await run_scenario(scenario, recorder=rec, builder=builder)
        rec.flush()
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
    sidecar = _write_sidecar(out_path, no_sidecar, builder, media)
    typer.echo(f"✔ {enc.path} ({enc.size_bytes // 1024} KB, {enc.frame_count} frames)")
    if not result.ok:
        typer.secho(
            f"! scenario failed at step {result.failed_at}",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if sidecar:
        typer.echo(f"  sidecar: {sidecar}")
    if not result.ok:
        raise typer.Exit(code=1)


# ==========================================================================
# clickcast shot
# ==========================================================================


@app.command(help="Capture a single screenshot.")
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
    viewport: Viewport = "1280x800",
    device: Device = None,
    engine: Engine = "chromium",
    dark: Dark = False,
) -> None:
    asyncio.run(
        _do_shot(
            url=url,
            out=out,
            full_page=full_page,
            wait=wait,
            session_kwargs=_session_kwargs(engine, viewport, device, False, None, dark),
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


@app.command(help="Scaffold a starter scenario file.")
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


@app.command(help="Dump interactive elements clickcast can see on a page.")
def elements(
    url: Annotated[str, typer.Argument(help="Target URL.")],
    limit: Annotated[int, typer.Option("--limit", help="Cap on returned elements.")] = 20,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON on stdout.")
    ] = False,
    viewport: Viewport = "1280x800",
    engine: Engine = "chromium",
) -> None:
    result_elements = asyncio.run(
        _do_elements(
            url=url,
            limit=limit,
            session_kwargs=_session_kwargs(engine, viewport, None, False, None, False),
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


@app.command(help="Diagnose the local environment.")
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    report = _run_doctor_checks()
    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        for check in report["checks"]:
            marker = "✔" if check["ok"] else "✗"
            colour = typer.colors.GREEN if check["ok"] else typer.colors.RED
            typer.secho(f"  {marker} {check['name']}: {check['detail']}", fg=colour)
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


@app.command(help="Read / write persistent defaults.")
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


@app.command(help="Install browser engines (wraps `playwright install`).")
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


if __name__ == "__main__":
    app()
