from __future__ import annotations

import typer

from clickcast import __version__

app = typer.Typer(
    name="clickcast",
    help="Drive a browser through a website and return a reel + AI-readable feedback sidecar.",
    no_args_is_help=True,
    add_completion=False,
)


def _not_yet(name: str) -> None:
    typer.secho(
        f"`clickcast {name}` is not implemented yet — tracked in the MVP roadmap (issue #1).",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        is_eager=True,
        callback=_version_callback,
    ),
) -> None:
    pass


@app.command(help="Auto-discover interactive elements and record a tour.")
def auto(url: str = typer.Argument(..., help="Target URL.")) -> None:
    _not_yet("auto")


@app.command(help="Run a YAML scenario end-to-end.")
def run(scenario: str = typer.Argument(..., help="Path to a scenario file.")) -> None:
    _not_yet("run")


@app.command(help="Capture a single screenshot.")
def shot(url: str = typer.Argument(..., help="Target URL.")) -> None:
    _not_yet("shot")


@app.command(help="Scaffold a starter scenario file.")
def init(path: str = typer.Argument("tour.yml", help="Output scenario path.")) -> None:
    _not_yet("init")


@app.command(help="Dump interactive elements clickcast can see on a page.")
def elements(url: str = typer.Argument(..., help="Target URL.")) -> None:
    _not_yet("elements")


@app.command(help="Diagnose the local environment.")
def doctor() -> None:
    _not_yet("doctor")


@app.command(help="Read / write persistent defaults.")
def config(action: str = typer.Argument(..., help="get | set | path")) -> None:
    _not_yet("config")


@app.command(help="Install browser engines (thin wrapper over `playwright install`).")
def install(engines: list[str] = typer.Argument(None, help="Engines to install.")) -> None:
    _not_yet("install")


if __name__ == "__main__":
    app()
