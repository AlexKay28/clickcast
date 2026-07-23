"""Clickcast CLI — Typer app wiring every command promised in the README.

Command modules stay thin — each dispatches into `clickcast.core`,
`clickcast.scenario`, `clickcast.discover`, or `clickcast.encode`. Business
logic lives in those subsystems, not here.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from platformdirs import user_config_dir

from clickcast import __version__
from clickcast.capture import Recorder
from clickcast.core.actions import ClickStep, GotoStep, ScrollStep, execute
from clickcast.core.session import Session
from clickcast.discover import Element, discover
from clickcast.encode import encode
from clickcast.scenario import ScenarioError, load
from clickcast.scenario import run as run_scenario

_APP_NAME = "clickcast"


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


def _write_sidecar_stub(out: Path, no_sidecar: bool, extra: dict[str, Any]) -> Path | None:
    """Placeholder sidecar writer until #12 lands the real Report model.

    We keep the flag surface real (``--no-sidecar``) and write the smallest
    plausible JSON so downstream consumers don't crash on missing files.
    """
    if no_sidecar:
        return None
    sidecar = out.with_suffix(out.suffix + ".json")
    payload = {
        "schema_version": 0,  # not v1 yet — #12 replaces this
        "note": "placeholder sidecar; full schema lands in #12",
        **extra,
    }
    sidecar.write_text(json.dumps(payload, indent=2))
    return sidecar


# ==========================================================================
# Top-level
# ==========================================================================


@app.callback()
def _root(
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
    pass


# ==========================================================================
# clickcast auto
# ==========================================================================


@app.command(help="Auto-discover interactive elements and record a tour.")
def auto(
    url: Annotated[str, typer.Argument(help="Target URL.")],
    out: OutOpt = "reel.gif",
    max_steps: Annotated[
        int, typer.Option("--max-steps", "-N", help="Cap on how many elements to click.")
    ] = 10,
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
    asyncio.run(
        _do_auto(
            url=url,
            out=out,
            max_steps=max_steps,
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


async def _do_auto(
    *,
    url: str,
    out: str,
    max_steps: int,
    dwell: float,
    initial_wait: float,
    session_kwargs: dict[str, Any],
    fps: int,
    format_: str | None,
    quality: int,
    loop: int,
    no_sidecar: bool,
) -> None:
    async with Session(**session_kwargs) as sess:
        with Recorder(fps=fps, default_dwell=dwell) as rec:
            goto = GotoStep(url=url, wait="networkidle", dwell=dwell)
            await rec.pre_action(sess)
            result = await execute(goto, sess)
            if not result.ok:
                _die(f"goto {url} failed: {result.error}")
            if initial_wait > 0:
                await sess.wait(initial_wait)
            await rec.post_action(sess, result, goto)

            elements = await discover(sess, limit=max_steps * 2)
            if not elements:
                _die("no interactive elements discovered")

            clicked = 0
            for element in elements:
                if clicked >= max_steps:
                    break
                step = ClickStep(
                    selector=element.selector,
                    dwell=dwell,
                    optional=True,
                    label=element.text[:60] or element.role,
                )
                await rec.pre_action(sess)
                r = await execute(step, sess)
                await rec.post_action(sess, r, step)
                if r.status == "ok":
                    clicked += 1
                await sess.wait(0.3)

            scroll = ScrollStep(by=600, dwell=dwell)
            await rec.pre_action(sess)
            r = await execute(scroll, sess)
            await rec.post_action(sess, r, scroll)

            rec.flush()
            out_path = Path(out)
            enc = encode(
                rec.frames_dir,
                out_path,
                fps=fps,
                quality=quality,
                loop=loop,
                format=format_,  # type: ignore[arg-type]
            )
            sidecar = _write_sidecar_stub(
                out_path,
                no_sidecar,
                {
                    "url": url,
                    "media": {
                        "path": str(enc.path),
                        "format": enc.format,
                        "size_bytes": enc.size_bytes,
                        "frame_count": enc.frame_count,
                        "duration_s": enc.duration_s,
                    },
                    "discovered_elements": [e.to_dict() for e in elements[:max_steps]],
                },
            )

    typer.echo(
        f"✔ {enc.path} ({enc.size_bytes // 1024} KB, {enc.frame_count} frames, {enc.duration_s:.1f}s)"
    )
    if sidecar:
        typer.echo(f"  sidecar: {sidecar}")


# ==========================================================================
# clickcast run
# ==========================================================================


@app.command(help="Run a YAML scenario end-to-end.")
def run(
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

    # CLI overrides on top of scenario.meta
    meta = scenario.meta
    final_out = out or meta.out
    if headful:
        meta.headful = True
    if slowmo:
        meta.slowmo = slowmo

    asyncio.run(
        _do_run(
            scenario=scenario,
            out=final_out,
            format_=format or meta.format,
            no_sidecar=no_sidecar,
        )
    )


async def _do_run(
    *,
    scenario: Any,
    out: str,
    format_: str | None,
    no_sidecar: bool,
) -> None:
    with Recorder(fps=scenario.meta.fps, default_dwell=scenario.meta.dwell) as rec:
        result = await run_scenario(scenario, recorder=rec)
        rec.flush()
        out_path = Path(out)
        enc = encode(
            rec.frames_dir,
            out_path,
            fps=scenario.meta.fps,
            format=format_,  # type: ignore[arg-type]
        )
    sidecar = _write_sidecar_stub(
        out_path,
        no_sidecar,
        {
            "media": {
                "path": str(enc.path),
                "format": enc.format,
                "size_bytes": enc.size_bytes,
                "frame_count": enc.frame_count,
                "duration_s": enc.duration_s,
            },
            "steps_ok": result.ok,
            "failed_at": result.failed_at,
        },
    )
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
    action: Annotated[str, typer.Argument(help="path | get | set")],
    key: Annotated[str | None, typer.Argument(help="Config key (for get / set).")] = None,
    value: Annotated[str | None, typer.Argument(help="Value (for set).")] = None,
) -> None:
    config_path = Path(user_config_dir(_APP_NAME)) / "config.toml"
    if action == "path":
        typer.echo(str(config_path))
        return
    if action in {"get", "set"}:
        typer.secho(
            f"`clickcast config {action}` requires the config-precedence layer (#13). "
            f"For now, edit {config_path} directly or use scenario `meta:` blocks.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=2)
    raise typer.BadParameter(f"unknown action {action!r}; expected path | get | set")


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
