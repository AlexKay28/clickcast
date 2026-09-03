"""Locate installed Playwright browser engines on disk.

Extracted from ``cli.py`` (where it originally backed ``clickcast doctor``)
so :class:`~clickcast.core.session.Session` can run the same check before
launching a browser — one detection table, two consumers: a diagnostic
report and a friendly pre-flight error instead of Playwright's raw
``Executable doesn't exist`` traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["EngineNotInstalledError", "find_installed_engine"]


class EngineNotInstalledError(RuntimeError):
    """Raised when a Playwright browser engine isn't installed locally.

    Carries the engine name so callers (the CLI's install-and-retry prompt,
    the MCP server's error payload) can build their own message without
    parsing ``str(exc)``.
    """

    def __init__(self, engine: str) -> None:
        self.engine = engine
        super().__init__(f"{engine} isn't installed. Run: clickcast install --with-deps {engine}")


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
    cache_root = Path.home() / ".cache" / "ms-playwright"
    if not cache_root.exists():
        alt = Path.home() / "Library" / "Caches" / "ms-playwright"
        cache_root = alt if alt.exists() else cache_root
    if not cache_root.exists():
        return None
    prefix = {"chromium": "chromium", "firefox": "firefox", "webkit": "webkit"}.get(engine)
    if not prefix:
        return None
    # Restrict to `<prefix>-<numeric-version>` dirs. This filters out sibling
    # installs that share the prefix but have a different exec layout, e.g.
    # `chromium-headless-shell-*` and `chromium-tip-of-tree-*`.
    matches = sorted(
        p
        for p in cache_root.glob(f"{prefix}-*")
        if p.is_dir() and p.name[len(prefix) + 1 :].isdigit()
    )
    if not matches:
        return None
    install_dir = matches[-1]
    for parts in _candidate_executable_parts(engine):
        candidate = install_dir.joinpath(*parts)
        if candidate.exists():
            return candidate, "executable"
    # Novel layout: don't lie about what we found.
    return install_dir, "install dir"
