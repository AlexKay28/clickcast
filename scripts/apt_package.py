"""Render the Debian control tree + apt repo index files for the `.deb`.

This is release tooling (not part of the installable ``clickcast`` package),
in the same spirit as ``scripts/gen_feedback_schema.py`` /
``scripts/homebrew_formula.py``. It's used by ``scripts/build_deb.sh`` (which
stages a self-contained venv under ``/opt/clickcast`` and calls this script to
render ``DEBIAN/control`` + ``DEBIAN/postinst``) and by
``.github/workflows/apt-release.yml`` (which, when ``APT_SIGNING_KEY`` is
configured, also uses the ``packages-entry`` / ``release`` subcommands to
build the self-hosted apt repo's `Packages` / `Release` index files).

Packaging design (see docs/packaging/apt.md for the full rationale):

- The `.deb` bundles a full venv under `/opt/clickcast/venv`, with
  `/usr/bin/clickcast` symlinked to the venv's entry point. This sidesteps
  issue #170's own concern that Debian's `python3` alias varies release to
  release (bookworm=3.11, trixie=3.12) -- clickcast's `>=3.10` floor and
  exact pinned dependencies travel with the package rather than depending on
  whatever `python3` happens to resolve to on the target.
- `Depends: python3 (>= 3.10), python3-venv` is still declared: the bundled
  venv's interpreter is a symlink back to the system `python3` used at build
  time (CPython's venv module doesn't copy the interpreter binary itself),
  so a compatible system Python must exist on the target. Building on a
  current Ubuntu LTS runner keeps this aligned with the widest realistic set
  of Debian/Ubuntu targets for v1; see docs/packaging/apt.md for the
  known limitation and follow-up options (`--copies`, PyInstaller, dh-virtualenv).
- No `ffmpeg` in `Depends:` -- `imageio[ffmpeg]` already bundles one on the
  Python side (issue #170: "pick one to avoid two ffmpegs on disk").
- No Chromium anywhere in the package -- `postinst` prints the same
  `clickcast install --with-deps chromium` caveat the Homebrew formula does.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

# datetime.UTC is 3.11+; project's requires-python is 3.10.
UTC = timezone.utc

MAINTAINER = "Alexander Kaigorodov <55444371+AlexKay28@users.noreply.github.com>"
HOMEPAGE = "https://github.com/AlexKay28/clickcast"
# Debian convention caps the control-file synopsis line around 60-80 chars
# (lintian's `synopsis-too-long` flags anything longer); the fuller
# description lives in DESCRIPTION_BODY below instead.
SUMMARY = "Browser automation reel + AI-readable feedback sidecar"
DESCRIPTION_BODY = (
    "clickcast automates a real browser via Playwright to produce a watchable\n"
    "GIF/MP4/WebP reel and a machine-readable JSON sidecar (selectors, timings,\n"
    "page state, discovered elements) for AI agents and CI regression gates."
)


def _indent_description(body: str) -> str:
    """Indent each line of a Debian control long description by one space.

    A blank line in the body must become a lone " ." -- a truly empty line
    would terminate the field.
    """
    lines = []
    for line in body.splitlines():
        lines.append(f" {line}" if line else " .")
    return "\n".join(lines)


def render_control(*, version: str, architecture: str) -> str:
    """Render the `DEBIAN/control` file for the given version/architecture."""
    description = _indent_description(DESCRIPTION_BODY)
    return (
        "Package: clickcast\n"
        f"Version: {version}\n"
        "Section: utils\n"
        "Priority: optional\n"
        f"Architecture: {architecture}\n"
        # python3-venv: the bundled venv's interpreter is a symlink back to
        # the system python3 used at build time (see module docstring).
        "Depends: python3 (>= 3.10), python3-venv\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Homepage: {HOMEPAGE}\n"
        f"Description: {SUMMARY}\n"
        f"{description}\n"
    )


def render_postinst() -> str:
    """Render `DEBIAN/postinst` -- prints the Chromium caveat, like the
    Homebrew formula's `caveats` block does."""
    return (
        "#!/bin/sh\n"
        "set -e\n"
        "\n"
        "cat <<'MSG'\n"
        "clickcast is installed. Chromium is NOT bundled (it's a ~180MB\n"
        "download that changes independently of clickcast releases) --\n"
        "install it once:\n"
        "\n"
        "    clickcast install --with-deps chromium\n"
        "\n"
        "ffmpeg IS bundled, via the Python imageio[ffmpeg] dependency inside\n"
        "this package's venv -- no system ffmpeg package is required.\n"
        "MSG\n"
        "\n"
        "exit 0\n"
    )


def file_size_and_sha256(path: Path) -> tuple[int, str]:
    """Return `(size_in_bytes, sha256_hexdigest)` for a file on disk."""
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def render_packages_stanza(
    *, version: str, architecture: str, filename: str, size: int, sha256: str
) -> str:
    """Render one `Packages` file stanza (control fields + apt-repo pool metadata).

    Trailing blank line included, since `Packages` is a multi-stanza file
    (RFC822-style, stanzas separated by a blank line).
    """
    control = render_control(version=version, architecture=architecture).rstrip("\n")
    return f"{control}\nFilename: {filename}\nSize: {size}\nSHA256: {sha256}\n\n"


def render_release(
    *,
    codename: str,
    components: list[str],
    architectures: list[str],
    entries: list[tuple[str, int, str]],
    origin: str = "clickcast",
    label: str = "clickcast",
) -> str:
    """Render the apt repo's top-level `Release` file (unsigned).

    `entries` is `[(relative_path, size, sha256), ...]` for every file under
    `dists/<codename>/` that should be checksummed (each arch's `Packages`
    and `Packages.gz`). Signing this into an `InRelease` / `Release.gpg` is a
    separate step, gated on the `APT_SIGNING_KEY` secret existing -- see
    docs/packaging/apt.md.
    """
    date = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S UTC")
    lines = [
        f"Origin: {origin}",
        f"Label: {label}",
        f"Suite: {codename}",
        f"Codename: {codename}",
        f"Architectures: {' '.join(architectures)}",
        f"Components: {' '.join(components)}",
        "Description: Self-hosted apt repository for clickcast (AlexKay28/clickcast)",
        f"Date: {date}",
        "SHA256:",
    ]
    for relpath, size, sha256 in entries:
        lines.append(f" {sha256} {size:>16} {relpath}")
    return "\n".join(lines) + "\n"


def _cmd_control(args: argparse.Namespace) -> int:
    sys.stdout.write(render_control(version=args.version, architecture=args.arch))
    return 0


def _cmd_postinst(_args: argparse.Namespace) -> int:
    sys.stdout.write(render_postinst())
    return 0


def _cmd_packages_entry(args: argparse.Namespace) -> int:
    deb_path = Path(args.deb)
    size, sha256 = file_size_and_sha256(deb_path)
    filename = f"pool/{args.component}/c/clickcast/{deb_path.name}"
    sys.stdout.write(
        render_packages_stanza(
            version=args.version,
            architecture=args.arch,
            filename=filename,
            size=size,
            sha256=sha256,
        )
    )
    return 0


def _cmd_release(args: argparse.Namespace) -> int:
    entries: list[tuple[str, int, str]] = []
    for raw in args.entry:
        relpath, size_str, sha256 = raw.rsplit(":", 2)
        entries.append((relpath, int(size_str), sha256))
    sys.stdout.write(
        render_release(
            codename=args.codename,
            components=args.component,
            architectures=args.arch,
            entries=entries,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_control = sub.add_parser("control", help="render DEBIAN/control")
    p_control.add_argument("--version", required=True)
    p_control.add_argument("--arch", required=True)
    p_control.set_defaults(func=_cmd_control)

    p_postinst = sub.add_parser("postinst", help="render DEBIAN/postinst")
    p_postinst.set_defaults(func=_cmd_postinst)

    p_entry = sub.add_parser("packages-entry", help="render one Packages stanza for a built .deb")
    p_entry.add_argument("--deb", required=True, help="path to the built .deb file")
    p_entry.add_argument("--version", required=True)
    p_entry.add_argument("--arch", required=True)
    p_entry.add_argument("--component", default="main")
    p_entry.set_defaults(func=_cmd_packages_entry)

    p_release = sub.add_parser("release", help="render the dists/<codename>/Release file")
    p_release.add_argument("--codename", required=True)
    p_release.add_argument("--component", action="append", required=True, dest="component")
    p_release.add_argument("--arch", action="append", required=True, dest="arch")
    p_release.add_argument(
        "--entry",
        action="append",
        default=[],
        metavar="RELPATH:SIZE:SHA256",
        help="a dists/<codename>/-relative file to checksum, e.g. "
        "main/binary-amd64/Packages:1234:<sha256>. Repeatable.",
    )
    p_release.set_defaults(func=_cmd_release)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
