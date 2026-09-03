"""Render the `Formula/clickcast.rb` Homebrew formula for a given release.

Run (fetches the sdist URL + sha256 from PyPI automatically):

    python scripts/homebrew_formula.py 0.3.0 --out Formula/clickcast.rb

Or pin them explicitly (useful offline / in tests, or to target TestPyPI):

    python scripts/homebrew_formula.py 0.3.0 \\
        --url https://files.pythonhosted.org/packages/.../clickcast-0.3.0.tar.gz \\
        --sha256 <sha256> \\
        --out Formula/clickcast.rb

This is release tooling (not part of the installable ``clickcast`` package),
in the same spirit as ``scripts/gen_feedback_schema.py``. It's used two ways:

1. Locally, to regenerate the formula checked into this repo's ``Formula/``
   directory (what ``brew install --build-from-source ./Formula/clickcast.rb``
   builds against).
2. By ``.github/workflows/homebrew-tap.yml`` on every published GitHub
   release, to render the formula for the new version and push it to the
   ``AlexKay28/homebrew-clickcast`` tap repo.

Formula design (see docs/packaging/homebrew.md for the full rationale):

- Uses Homebrew's ``Language::Python::Virtualenv`` mixin with a *single*
  ``venv.pip_install "clickcast==#{version}"`` rather than a fully pinned
  ``resource`` block per transitive dependency. That's the documented
  simplification issue #170 explicitly allows for v1 ("pick one after
  prototyping") -- generating/maintaining a `resource` block per transitive
  dependency (playwright, Pillow, imageio, pydantic, ...) on every release is
  heavy for day one and can be revisited once the tap has traction.
- Never declares a system ``ffmpeg`` dependency -- ``imageio[ffmpeg]``
  already bundles one on the Python side (issue #170: "pick one to avoid two
  ffmpegs on disk").
- Never bundles Chromium (~200MB, versioned independently of clickcast
  releases) -- the ``caveats`` block points at the existing
  ``clickcast install --with-deps chromium`` command instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from string import Template
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

PYPI_JSON_URL = "https://pypi.org/pypi/clickcast/{version}/json"

# `$version`/`$url`/`$sha256` are substituted by Python's `string.Template`.
# Everything written as `#{...}` below is Ruby string interpolation, evaluated
# by Homebrew at *install* time (e.g. `#{version}`, `#{bin}`) -- it must be
# left untouched, which is exactly why Template (not str.format / f-strings)
# is used here: it only ever touches `$identifier` placeholders.
_FORMULA_TEMPLATE = Template(
    """class Clickcast < Formula
  include Language::Python::Virtualenv

  desc "Drive a browser through a website and hand back a reel + AI-readable feedback sidecar"
  homepage "https://github.com/AlexKay28/clickcast"
  url "$url"
  sha256 "$sha256"
  license "MIT"

  depends_on "python@3.12"

  # Deliberately no system ffmpeg dependency line here: clickcast bundles one
  # via the Python `imageio[ffmpeg]` dependency already pulled in below.
  # Declaring a system ffmpeg here would put a second copy on disk for no
  # benefit -- see docs/packaging/homebrew.md.
  #
  # No Chromium here either -- it's a ~200MB download that changes on its own
  # release cadence, independent of clickcast's. Post-install `caveats` below
  # points at the existing `clickcast install` command instead.

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install "clickcast==#{version}"

    bin.install_symlink libexec/"bin/clickcast"
  end

  def caveats
    <<~EOS
      clickcast needs a Chromium browser (~180MB) that this formula does not
      bundle -- browsers are versioned independently of clickcast releases.
      Install it once:

        clickcast install --with-deps chromium

      ffmpeg is bundled via the Python `imageio[ffmpeg]` dependency; no
      system ffmpeg package is installed or required by this formula.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/clickcast --version").strip
  end
end
"""
)


class PypiLookupError(RuntimeError):
    """Raised when PyPI doesn't have the sdist this formula needs to pin."""


def render_formula(*, version: str, url: str, sha256: str) -> str:
    """Render the Homebrew formula body for `version`, pinned to `url`/`sha256`.

    `version` only needs to be a valid PEP 440 string for the caller's own
    bookkeeping -- Homebrew derives its own `version` from the `url` at
    install time, so it isn't substituted into the template directly.
    """
    return _FORMULA_TEMPLATE.substitute(url=url, sha256=sha256)


def fetch_pypi_sdist(
    version: str,
    *,
    opener: Callable[..., object] = urlopen,
    timeout: float = 10.0,
) -> tuple[str, str]:
    """Look up the sdist URL + sha256 for `clickcast==version` on PyPI.

    Returns `(url, sha256)`. Raises `PypiLookupError` if the version isn't
    published yet or was published without a sdist (only a wheel).
    """
    url = PYPI_JSON_URL.format(version=version)
    try:
        response = opener(url, timeout=timeout)
    except HTTPError as exc:
        raise PypiLookupError(
            f"PyPI has no release {version} for clickcast yet (HTTP {exc.code} on {url})"
        ) from exc
    except URLError as exc:
        raise PypiLookupError(f"could not reach PyPI at {url}: {exc.reason}") from exc

    with response as fh:
        payload = json.loads(fh.read())

    for entry in payload.get("urls", []):
        if entry.get("packagetype") == "sdist":
            return entry["url"], entry["digests"]["sha256"]

    raise PypiLookupError(f"clickcast {version} has no sdist on PyPI (only wheel(s)?)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="clickcast version to pin, e.g. 0.3.0")
    parser.add_argument("--url", help="sdist URL to pin (must be given together with --sha256)")
    parser.add_argument("--sha256", help="sdist sha256 to pin (must be given together with --url)")
    parser.add_argument(
        "--out",
        default="Formula/clickcast.rb",
        help="path to write the formula to (default: Formula/clickcast.rb)",
    )
    args = parser.parse_args(argv)

    if bool(args.url) != bool(args.sha256):
        parser.error("--url and --sha256 must be given together, or not at all")

    if args.url and args.sha256:
        url, sha256 = args.url, args.sha256
    else:
        url, sha256 = fetch_pypi_sdist(args.version)

    content = render_formula(version=args.version, url=url, sha256=sha256)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content)
    print(f"wrote {out_path} (clickcast {args.version}, sha256 {sha256[:12]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
