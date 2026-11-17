"""Mechanical layering guards (#260).

The consolidation work relies on a handful of invariants that used to hold
only by convention. These tests AST-parse every module under ``src/clickcast``
(``ast.walk``, so imports hidden inside function bodies are caught too) rather
than grepping, and fail with the offending file and line.

Four invariants:

1. Playwright may only be imported by the session layer (the real browser API).
2. The ``clickcast.encode.encode()`` callable is only called by the render
   kernel (plus ``reel.py`` until #261 routes the SDK through the kernel).
   The facade's result *types* (``EncodeResult``, ``Format``) are plain data
   and may be imported anywhere.
3. Engine/layer packages never import the CLI framework (``typer``).
4. Nothing under ``src/`` imports ``clickcast.cli`` except the entry point.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[1] / "src" / "clickcast"
_REPO_ROOT = _SRC.parent.parent

#: Modules allowed to import Playwright (directly or via ``playwright.*``).
#:
#: ``core/session.py`` is the narrow seam that owns launch/context/page and
#: re-exports the few Playwright types callers need. ``core/engines.py`` is
#: deliberately *not* here: it probes the install with
#: ``importlib.util.find_spec`` and reads metadata, so it never imports
#: Playwright as a value.
_PLAYWRIGHT_ALLOWLIST = {
    "core/session.py": "the single browser seam; owns Playwright launch and page lifecycle",
}

#: Modules allowed to import the ``encode()`` callable from the
#: ``clickcast.encode`` facade. Type-only imports (``EncodeResult``, ``Format``)
#: are not restricted: they carry no encoder behaviour.
_ENCODE_ALLOWLIST = {
    "kernel/render.py": "the one render pipeline; the only supported encoder caller",
    # TODO(#261): the SDK still encodes directly. Drop this entry once reel.py
    # hands frames to the kernel instead of calling encode() itself.
    "reel.py": "direct SDK encoding, to be narrowed by #261",
}

#: Packages that must stay free of the CLI framework. (``feedback/session``
#: runs one level deeper, hence the allowlist entry below.)
_LAYER_DIRS = (
    "core",
    "capture",
    "annotate",
    "encode",
    "scenario",
    "discovery",
    "feedback",
    "mcp",
    "kernel",
)

#: Typer adapters that live inside a layer package. ``feedback/session/cli.py``
#: is the ``clickcast feedback`` sub-app registered on the root app (#124) —
#: a CLI adapter, not engine code; the engine modules beside it stay typer-free.
_TYPER_ALLOWLIST = {
    "feedback/session/cli.py": "Typer sub-app adapter registered on the root app (#124)",
}

#: The console-script entry point necessarily boots ``clickcast.cli``.
_CLI_ALLOWLIST = {
    "__main__.py": "console entry point that delegates to clickcast.cli:main",
}


def _all_modules() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


def _display(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _resolved_module(path: Path, node: ast.ImportFrom) -> str | None:
    """Absolute module name for an ``ImportFrom``, resolving relative imports."""
    if not node.level:
        return node.module
    parts = list(path.relative_to(_SRC.parent).with_suffix("").parts)
    if path.name != "__init__.py":
        parts = parts[:-1]
    # level 1 == the containing package, level 2 == its parent, …
    base = parts[: len(parts) - (node.level - 1)]
    if node.module:
        base.append(node.module)
    return ".".join(base)


def _imports_detailed(path: Path) -> Iterator[tuple[str, int, frozenset[str]]]:
    """Yield ``(module, line, imported_names)`` for every import in ``path``.

    ``imported_names`` is the set of names bound from that module (``"*"`` for
    a star import); ``ast.Import`` yields the dotted name itself.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno, frozenset({alias.name})
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_module(path, node)
            if module:
                yield module, node.lineno, frozenset(a.name for a in node.names)


def _imports(path: Path) -> Iterator[tuple[str, int]]:
    """Yield ``(module, line)`` for every import anywhere in ``path``."""
    for module, line, _names in _imports_detailed(path):
        yield module, line


def _matches(module: str, root: str, *, include_submodules: bool = True) -> bool:
    return module == root or (include_submodules and module.startswith(f"{root}."))


def _violations(
    paths: Iterable[Path],
    predicate: Callable[[str], bool],
    allowlist: dict[str, str],
) -> list[str]:
    found: list[str] = []
    for path in paths:
        rel = path.relative_to(_SRC).as_posix()
        if rel in allowlist:
            continue
        for module, line in _imports(path):
            if predicate(module):
                found.append(f"{_display(path)}:{line}: imports {module}")
    return found


def _layered_modules() -> list[Path]:
    return [path for path in _all_modules() if path.relative_to(_SRC).parts[0] in _LAYER_DIRS]


def _assert_no(violations: list[str], rule: str) -> None:
    assert not violations, f"{rule}:\n  " + "\n  ".join(violations)


def test_playwright_is_confined_to_the_session_seam() -> None:
    violations = _violations(
        _all_modules(),
        lambda module: _matches(module, "playwright"),
        _PLAYWRIGHT_ALLOWLIST,
    )
    _assert_no(
        violations,
        "Playwright may only be imported by "
        + ", ".join(sorted(_PLAYWRIGHT_ALLOWLIST))
        + " (use clickcast.core.engines.playwright_importable() to probe the install)",
    )


def test_encode_callable_has_one_caller() -> None:
    """Only the render kernel may *reach the encoder*, not merely name its types.

    Exact-module match: ``clickcast/encode/__init__.py`` importing its own
    submodule is the facade's own wiring, not a caller of the facade.
    """
    violations: list[str] = []
    for path in _all_modules():
        rel = path.relative_to(_SRC).as_posix()
        if rel in _ENCODE_ALLOWLIST:
            continue
        for module, line, names in _imports_detailed(path):
            if module == "clickcast.encode" and ("encode" in names or "*" in names):
                violations.append(f"{_display(path)}:{line}: imports encode from clickcast.encode")
    _assert_no(
        violations,
        "clickcast.encode.encode() may only be called by "
        + ", ".join(sorted(_ENCODE_ALLOWLIST))
        + " (types EncodeResult/Format are unrestricted)",
    )


def test_layer_packages_do_not_import_typer() -> None:
    violations = _violations(
        _layered_modules(),
        lambda module: _matches(module, "typer"),
        _TYPER_ALLOWLIST,
    )
    _assert_no(
        violations,
        "engine/layer modules must not depend on typer; move CLI wiring into a cli module",
    )


def test_nothing_imports_the_cli() -> None:
    modules = [path for path in _all_modules() if path.name != "cli.py"]
    violations = _violations(
        modules,
        lambda module: _matches(module, "clickcast.cli"),
        _CLI_ALLOWLIST,
    )
    _assert_no(
        violations,
        "nothing under src/ may import clickcast.cli (except the console entry point)",
    )


@pytest.mark.parametrize(
    "allowlist",
    [_PLAYWRIGHT_ALLOWLIST, _ENCODE_ALLOWLIST, _TYPER_ALLOWLIST, _CLI_ALLOWLIST],
    ids=["playwright", "encode", "typer", "cli"],
)
def test_allowlist_entries_still_exist(allowlist: dict[str, str]) -> None:
    """Keep the allowlists honest: a stale entry hides future regressions."""
    missing = [rel for rel in allowlist if not (_SRC / rel).is_file()]
    assert not missing, f"stale allowlist entries (no such module): {missing}"
