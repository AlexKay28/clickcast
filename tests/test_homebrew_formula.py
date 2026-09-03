"""Tests for ``scripts/homebrew_formula.py``.

The script isn't part of the installable ``clickcast`` package (it's release
tooling, like ``scripts/gen_feedback_schema.py``), so it's loaded here by file
path rather than import — see ``_load_module`` below.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "homebrew_formula.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("homebrew_formula", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


homebrew_formula = _load_module()


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _pypi_payload(version: str, *, include_sdist: bool = True) -> dict:
    urls = [
        {
            "packagetype": "bdist_wheel",
            "filename": f"clickcast-{version}-py3-none-any.whl",
            "url": f"https://files.pythonhosted.org/packages/aa/bb/clickcast-{version}-py3-none-any.whl",
            "digests": {"sha256": "f" * 64},
        }
    ]
    if include_sdist:
        urls.append(
            {
                "packagetype": "sdist",
                "filename": f"clickcast-{version}.tar.gz",
                "url": f"https://files.pythonhosted.org/packages/cc/dd/clickcast-{version}.tar.gz",
                "digests": {"sha256": "e" * 64},
            }
        )
    return {"urls": urls}


class TestRenderFormula:
    def test_contains_pypi_url_and_sha256(self) -> None:
        content = homebrew_formula.render_formula(
            version="0.3.0",
            url="https://files.pythonhosted.org/packages/cc/dd/clickcast-0.3.0.tar.gz",
            sha256="e" * 64,
        )
        assert (
            'url "https://files.pythonhosted.org/packages/cc/dd/clickcast-0.3.0.tar.gz"' in content
        )
        assert f'sha256 "{"e" * 64}"' in content

    def test_pins_python_and_no_ffmpeg_dependency(self) -> None:
        content = homebrew_formula.render_formula(version="0.3.0", url="u", sha256="e" * 64)
        assert 'depends_on "python@3.12"' in content
        # ffmpeg ships via the Python imageio[ffmpeg] extra -- the formula
        # must never declare a system ffmpeg dependency (issue #170: "pick
        # one to avoid two ffmpegs on disk").
        assert 'depends_on "ffmpeg"' not in content

    def test_caveats_mention_chromium_install(self) -> None:
        content = homebrew_formula.render_formula(version="0.3.0", url="u", sha256="e" * 64)
        assert "clickcast install --with-deps chromium" in content

    def test_pins_exact_version_via_pip(self) -> None:
        content = homebrew_formula.render_formula(version="0.3.0", url="u", sha256="e" * 64)
        assert 'venv.pip_install "clickcast==#{version}"' in content

    def test_test_block_checks_version(self) -> None:
        content = homebrew_formula.render_formula(version="0.3.0", url="u", sha256="e" * 64)
        assert "test do" in content
        assert "clickcast --version" in content

    def test_ruby_class_name_and_structure(self) -> None:
        content = homebrew_formula.render_formula(version="0.3.0", url="u", sha256="e" * 64)
        assert content.startswith("class Clickcast < Formula")
        assert content.rstrip().endswith("end")


class TestFetchPypiSdist:
    def test_returns_sdist_url_and_sha256(self) -> None:
        payload = _pypi_payload("0.3.0")

        def fake_urlopen(url: str, timeout: float = 10.0) -> _FakeResponse:
            assert "0.3.0" in url
            return _FakeResponse(payload)

        url, sha256 = homebrew_formula.fetch_pypi_sdist("0.3.0", opener=fake_urlopen)
        assert url == "https://files.pythonhosted.org/packages/cc/dd/clickcast-0.3.0.tar.gz"
        assert sha256 == "e" * 64

    def test_raises_when_no_sdist_published(self) -> None:
        payload = _pypi_payload("0.3.0", include_sdist=False)

        def fake_urlopen(url: str, timeout: float = 10.0) -> _FakeResponse:
            return _FakeResponse(payload)

        with pytest.raises(homebrew_formula.PypiLookupError, match="no sdist"):
            homebrew_formula.fetch_pypi_sdist("0.3.0", opener=fake_urlopen)

    def test_raises_helpful_error_on_404(self) -> None:
        def fake_urlopen(url: str, timeout: float = 10.0) -> _FakeResponse:
            raise HTTPError(url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

        with pytest.raises(homebrew_formula.PypiLookupError, match=r"0\.3\.0"):
            homebrew_formula.fetch_pypi_sdist("0.3.0", opener=fake_urlopen)


class TestMain:
    def test_writes_formula_with_explicit_url_and_sha256(self, tmp_path: Path) -> None:
        out = tmp_path / "clickcast.rb"
        rc = homebrew_formula.main(
            [
                "0.3.0",
                "--url",
                "https://files.pythonhosted.org/packages/cc/dd/clickcast-0.3.0.tar.gz",
                "--sha256",
                "e" * 64,
                "--out",
                str(out),
            ]
        )
        assert rc == 0
        content = out.read_text()
        assert "0.3.0" in content or "e" * 64 in content
        assert f'sha256 "{"e" * 64}"' in content

    def test_requires_both_url_and_sha256_together(self, tmp_path: Path) -> None:
        out = tmp_path / "clickcast.rb"
        with pytest.raises(SystemExit):
            homebrew_formula.main(["0.3.0", "--sha256", "e" * 64, "--out", str(out)])
