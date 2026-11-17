"""Locate installed Playwright browser engines on disk.

Extracted from ``cli.py`` (where it originally backed ``clickcast doctor``)
so :class:`~clickcast.core.session.Session` can run the same check before
launching a browser — one detection table, two consumers: a diagnostic
report and a friendly pre-flight error instead of Playwright's raw
``Executable doesn't exist`` traceback.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "EngineCheck",
    "EngineNotInstalledError",
    "EngineStaleError",
    "check_engine",
    "find_installed_engine",
    "playwright_importable",
    "playwright_version",
    "required_revision",
]


class EngineNotInstalledError(RuntimeError):
    """Raised when a Playwright browser engine isn't installed locally.

    Carries the engine name so callers (the CLI's install-and-retry prompt,
    the MCP server's error payload) can build their own message without
    parsing ``str(exc)``.
    """

    def __init__(self, engine: str) -> None:
        self.engine = engine
        super().__init__(f"{engine} isn't installed. Run: clickcast install --with-deps {engine}")


class EngineStaleError(EngineNotInstalledError):
    """Raised when a browser is installed but not the revision Playwright launches.

    A stale shared cache is the most common real-world state for anyone who has
    used Playwright before: an older ``chromium-<rev>`` sits on disk, the newly
    installed Playwright wants a newer one, and the old pre-flight check said
    "ok" anyway. Subclasses :class:`EngineNotInstalledError` so every existing
    install-and-retry path fixes it without extra plumbing.
    """

    def __init__(self, engine: str, found: str | None, required: str) -> None:
        self.found = found
        self.required = required
        RuntimeError.__init__(
            self,
            f"{engine} {found or ''} is out of date; the installed Playwright needs "
            f"{engine}-{required}. Run: clickcast install --with-deps {engine}",
        )
        self.engine = engine


# Per-engine, per-platform executable path components relative to the Playwright
# browser install directory (e.g. ~/.cache/ms-playwright/chromium-1234/).
#
# Sourced from Playwright's own driver bundle (see
# `playwright/driver/package/lib/coreBundle.js`, EXECUTABLE_PATHS). Kept in
# lock-step shape with the upstream table so future Playwright releases can be
# added without reinventing anything.
_ENGINE_EXECUTABLE_PARTS: dict[str, dict[str, tuple[str, ...]]] = {
    "chromium": {
        # Linux CFT ships as chrome-linux64/chrome on x64 and chrome-linux/chrome
        # on arm64; we try both and take whichever is on disk.
        "linux": ("chrome-linux", "chrome"),
        "linux-x64": ("chrome-linux64", "chrome"),
        "darwin": ("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
        "darwin-x64": (
            "chrome-mac-x64",
            "Google Chrome for Testing.app",
            "Contents",
            "MacOS",
            "Google Chrome for Testing",
        ),
        "darwin-arm64": (
            "chrome-mac-arm64",
            "Google Chrome for Testing.app",
            "Contents",
            "MacOS",
            "Google Chrome for Testing",
        ),
        "win32": ("chrome-win", "chrome.exe"),
        "win32-x64": ("chrome-win64", "chrome.exe"),
    },
    "firefox": {
        "linux": ("firefox", "firefox"),
        "darwin": ("firefox", "Nightly.app", "Contents", "MacOS", "firefox"),
        "win32": ("firefox", "firefox.exe"),
    },
    "webkit": {
        # Playwright ships webkit behind a `pw_run.sh` launcher on POSIX (it
        # sets up LD_LIBRARY_PATH / DYLD_FRAMEWORK_PATH before exec'ing the
        # real binary). The launcher IS the executable entry point.
        "linux": ("pw_run.sh",),
        "darwin": ("pw_run.sh",),
        "win32": ("Playwright.exe",),
    },
}


def _candidate_executable_parts(engine: str) -> list[tuple[str, ...]]:
    """Return the plausible executable path components for `engine` on the current OS."""
    engine_map = _ENGINE_EXECUTABLE_PARTS.get(engine)
    if not engine_map:
        return []
    plat = sys.platform  # "linux", "darwin", "win32"
    # Prefer arch-specific entries (they mirror upstream's linux-x64 /
    # darwin-arm64 etc.) but always fall through to the plain-platform default.
    keys: list[str] = []
    if plat == "linux":
        keys = ["linux-x64", "linux"]
    elif plat == "darwin":
        keys = ["darwin-arm64", "darwin-x64", "darwin"]
    elif plat == "win32":
        keys = ["win32-x64", "win32"]
    else:
        keys = [plat]
    seen: set[tuple[str, ...]] = set()
    out: list[tuple[str, ...]] = []
    for k in keys:
        parts = engine_map.get(k)
        if parts and parts not in seen:
            seen.add(parts)
            out.append(parts)
    return out


def _cache_root() -> Path:
    """Playwright's browser cache root, honouring ``PLAYWRIGHT_BROWSERS_PATH``."""
    configured_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured_root and configured_root != "0":
        return Path(configured_root).expanduser()
    cache_root = Path.home() / ".cache" / "ms-playwright"
    if not cache_root.exists():
        alt = Path.home() / "Library" / "Caches" / "ms-playwright"
        cache_root = alt if alt.exists() else cache_root
    return cache_root


def _engine_dirs(engine: str) -> list[tuple[int, Path]]:
    """Installed ``<engine>-<revision>`` dirs, newest first.

    Restricting to ``<prefix>-<digits>`` filters out sibling installs that share
    the prefix but have a different exec layout, e.g. ``chromium_headless_shell-*``
    and ``chromium-tip-of-tree-*``.
    """
    cache_root = _cache_root()
    if not cache_root.exists():
        return []
    prefix = {"chromium": "chromium", "firefox": "firefox", "webkit": "webkit"}.get(engine)
    if not prefix:
        return []
    matches = [
        (int(p.name[len(prefix) + 1 :]), p)
        for p in cache_root.glob(f"{prefix}-*")
        if p.is_dir() and p.name[len(prefix) + 1 :].isdigit()
    ]
    return sorted(matches, key=lambda item: item[0], reverse=True)


def playwright_importable() -> bool:
    """Whether the Playwright package is installed.

    The single probe used by ``clickcast doctor`` and ``report-bug``. It goes
    through :func:`importlib.util.find_spec` rather than ``import playwright``
    so this module never needs a value import and stays outside the Playwright
    import allowlist.
    """
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):  # pragma: no cover — broken install
        return False


def playwright_version() -> str | None:
    """Installed Playwright version, read from package metadata.

    Playwright does not export ``__version__``, so metadata is the reliable
    source (the same approach ``discovery.accessibility`` uses). Returns
    ``None`` when Playwright isn't installed.
    """
    try:
        return importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover — hard dep
        return None


def required_revision(engine: str) -> str | None:
    """Revision the *installed* Playwright driver bundle expects for ``engine``.

    Read from Playwright's ``driver/package/browsers.json`` via
    :func:`importlib.util.find_spec` — no ``import playwright`` here, so this
    module stays outside the Playwright import allowlist.

    Returns ``None`` when Playwright isn't installed or ships no such entry, in
    which case callers must fall back to "any install is good enough".
    """
    try:
        spec = importlib.util.find_spec("playwright")
    except (ImportError, ValueError):  # pragma: no cover — broken install
        return None
    if spec is None or not spec.origin:
        return None
    manifest = Path(spec.origin).parent / "driver" / "package" / "browsers.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = {"chromium": "chromium", "firefox": "firefox", "webkit": "webkit"}.get(engine)
    if not name:
        return None
    for browser in data.get("browsers", []):
        if browser.get("name") == name:
            revision = browser.get("revision")
            return str(revision) if revision is not None else None
    return None


@dataclass(frozen=True)
class EngineCheck:
    """What ``check_engine`` found: installed-at-the-right-revision or not.

    ``state`` is ``"ok"`` (revision matches the installed Playwright),
    ``"stale"`` (a browser is on disk but the wrong revision) or ``"missing"``.
    ``headless_shell`` is the revision-matched headless-shell dir for chromium,
    reported so ``doctor`` can flag a ``--no-shell`` cache without failing.
    """

    engine: str
    state: str
    path: Path | None = None
    kind: str | None = None
    found_revision: str | None = None
    required_revision: str | None = None
    headless_shell: Path | None = None

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    @property
    def detail(self) -> str:
        if self.state == "ok":
            text = f"{self.kind}: {self.path}"
            if self.engine == "chromium" and self.required_revision and self.headless_shell is None:
                text += (
                    " (headless shell missing — headless runs need it;"
                    f" run `clickcast install {self.engine}`)"
                )
            return text
        if self.state == "stale":
            return (
                f"stale: found {self.engine}-{self.found_revision}, installed Playwright "
                f"needs {self.engine}-{self.required_revision} "
                f"(run `clickcast install {self.engine}`)"
            )
        return "not installed (run `clickcast install`)"


def _revision_of(engine: str, path: Path) -> str | None:
    """Extract the ``<rev>`` from a path inside ``<engine>-<rev>``."""
    for parent in (path, *path.parents):
        name = parent.name
        if name.startswith(f"{engine}-") and name[len(engine) + 1 :].isdigit():
            return name[len(engine) + 1 :]
    return None


def check_engine(engine: str) -> EngineCheck:
    """Verify the browser Playwright will *actually launch* is on disk.

    ``find_installed_engine`` answers "is any chromium on disk?" — good for a
    diagnostic listing, wrong for a pre-flight gate: a stale cache from an older
    Playwright passes it, then ``launch()`` dies with a raw
    ``Executable doesn't exist`` traceback. This checks the revision the
    installed Playwright expects.
    """
    required = required_revision(engine)
    dirs = _engine_dirs(engine)
    install = next((p for rev, p in dirs if required is None or str(rev) == required), None)
    if install is None:
        if not dirs:
            return EngineCheck(engine=engine, state="missing", required_revision=required)
        newest_rev, newest = dirs[0]
        return EngineCheck(
            engine=engine,
            state="stale",
            path=newest,
            found_revision=str(newest_rev),
            required_revision=required,
        )
    for parts in _candidate_executable_parts(engine):
        candidate = install.joinpath(*parts)
        if candidate.exists():
            kind: str = "executable"
            path = candidate
            break
    else:
        kind = "install dir"
        path = install
    shell = _cache_root() / f"chromium_headless_shell-{required}" if engine == "chromium" else None
    return EngineCheck(
        engine=engine,
        state="ok",
        path=path,
        kind=kind,
        found_revision=required or str(dirs[0][0]),
        required_revision=required,
        headless_shell=shell if shell is not None and shell.exists() else None,
    )


def find_installed_engine(engine: str) -> tuple[Path, str] | None:
    """Locate a Playwright browser install and (best-effort) its executable.

    Returns ``(path, kind)`` where ``kind`` is:

    - ``"executable"`` — ``path`` points at the actual browser binary (or the
      ``pw_run.sh`` launcher for webkit). This is what a user can invoke.
    - ``"install dir"`` — we found the browser's version directory under
      ``ms-playwright/`` but couldn't map it to a known executable layout
      (e.g. a novel install shape from a future Playwright release). ``path``
      is the install directory; the caller should label it as such rather than
      pretend it's runnable.

    Returns ``None`` if no matching browser is installed.
    """
    matches = [p for _rev, p in _engine_dirs(engine)]
    if not matches:
        return None
    for install_dir in matches:
        for parts in _candidate_executable_parts(engine):
            candidate = install_dir.joinpath(*parts)
            if candidate.exists():
                return candidate, "executable"
    # Novel layout: don't lie about what we found. Report the numerically
    # newest install, not the lexicographic maximum (999 > 1234 as strings).
    return matches[0], "install dir"
