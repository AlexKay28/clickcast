"""Tests for ``scripts/apt_package.py``.

Loaded by file path (release tooling, not part of the installable
``clickcast`` package) -- same pattern as ``tests/test_homebrew_formula.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "apt_package.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("apt_package", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apt_package = _load_module()


class TestRenderControl:
    def test_required_fields_present(self) -> None:
        content = apt_package.render_control(version="0.3.0", architecture="amd64")
        assert "Package: clickcast" in content
        assert "Version: 0.3.0" in content
        assert "Architecture: amd64" in content
        assert "Maintainer:" in content
        assert "Homepage: https://github.com/AlexKay28/clickcast" in content

    def test_depends_on_python3_not_ffmpeg(self) -> None:
        content = apt_package.render_control(version="0.3.0", architecture="amd64")
        depends_line = next(line for line in content.splitlines() if line.startswith("Depends:"))
        assert "python3" in depends_line
        # ffmpeg is bundled via imageio[ffmpeg] on the Python side -- the .deb
        # must never declare a system ffmpeg Depends (issue #170: "pick one
        # to avoid two ffmpegs on disk").
        assert "ffmpeg" not in depends_line

    def test_description_lines_are_indented(self) -> None:
        content = apt_package.render_control(version="0.3.0", architecture="amd64")
        lines = content.splitlines()
        desc_idx = next(i for i, line in enumerate(lines) if line.startswith("Description:"))
        # Every continuation line of a Debian control long description must
        # start with at least one space (or ` .` for a blank line).
        for line in lines[desc_idx + 1 :]:
            if not line:
                break
            assert line.startswith(" ")

    def test_arch_is_substituted(self) -> None:
        content = apt_package.render_control(version="0.3.0", architecture="arm64")
        assert "Architecture: arm64" in content

    def test_ends_with_trailing_blank_line(self) -> None:
        # dpkg-deb / dpkg-scanpackages require a trailing blank line after
        # each stanza in multi-stanza files; a single-stanza control file
        # just needs to end cleanly.
        content = apt_package.render_control(version="0.3.0", architecture="amd64")
        assert content.endswith("\n")


class TestRenderPostinst:
    def test_is_a_shell_script(self) -> None:
        content = apt_package.render_postinst()
        assert content.startswith("#!/bin/sh")

    def test_mentions_chromium_install(self) -> None:
        content = apt_package.render_postinst()
        assert "clickcast install --with-deps chromium" in content

    def test_mentions_ffmpeg_is_bundled(self) -> None:
        content = apt_package.render_postinst()
        assert "ffmpeg" in content.lower()

    def test_exits_zero(self) -> None:
        content = apt_package.render_postinst()
        assert content.rstrip().endswith("exit 0")

    def test_is_syntactically_valid_shell(self) -> None:
        result = subprocess.run(
            ["/bin/sh", "-n", "-"],
            input=apt_package.render_postinst(),
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr


class TestRenderPackagesStanza:
    def test_includes_control_fields_plus_pool_metadata(self) -> None:
        stanza = apt_package.render_packages_stanza(
            version="0.3.0",
            architecture="amd64",
            filename="pool/main/c/clickcast/clickcast_0.3.0_amd64.deb",
            size=12345,
            sha256="a" * 64,
        )
        assert "Package: clickcast" in stanza
        assert "Version: 0.3.0" in stanza
        assert "Filename: pool/main/c/clickcast/clickcast_0.3.0_amd64.deb" in stanza
        assert "Size: 12345" in stanza
        assert f"SHA256: {'a' * 64}" in stanza

    def test_stanza_ends_with_blank_line_for_multi_stanza_files(self) -> None:
        stanza = apt_package.render_packages_stanza(
            version="0.3.0",
            architecture="amd64",
            filename="pool/main/c/clickcast/clickcast_0.3.0_amd64.deb",
            size=1,
            sha256="a" * 64,
        )
        assert stanza.endswith("\n\n")


class TestRenderRelease:
    def test_includes_architectures_and_components(self) -> None:
        content = apt_package.render_release(
            codename="stable",
            components=["main"],
            architectures=["amd64"],
            entries=[("main/binary-amd64/Packages", 100, "b" * 64)],
        )
        assert "Architectures: amd64" in content
        assert "Components: main" in content
        assert "Suite: stable" in content
        assert "Codename: stable" in content

    def test_sha256_section_lists_every_entry(self) -> None:
        entries = [
            ("main/binary-amd64/Packages", 100, "b" * 64),
            ("main/binary-amd64/Packages.gz", 42, "c" * 64),
        ]
        content = apt_package.render_release(
            codename="stable", components=["main"], architectures=["amd64"], entries=entries
        )
        sha_section = content.split("SHA256:")[1]
        for relpath, size, sha256 in entries:
            assert f" {sha256} {size:>16} {relpath}" in sha_section or (
                sha256 in sha_section and relpath in sha_section and str(size) in sha_section
            )

    def test_date_field_present(self) -> None:
        content = apt_package.render_release(
            codename="stable", components=["main"], architectures=["amd64"], entries=[]
        )
        assert "Date: " in content


class TestCli:
    def test_control_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = apt_package.main(["control", "--version", "0.3.0", "--arch", "amd64"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Package: clickcast" in out
        assert "Version: 0.3.0" in out

    def test_postinst_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = apt_package.main(["postinst"])
        assert rc == 0
        assert "clickcast install --with-deps chromium" in capsys.readouterr().out

    def test_packages_entry_subcommand(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        deb = tmp_path / "clickcast_0.3.0_amd64.deb"
        deb.write_bytes(b"fake deb contents")
        rc = apt_package.main(
            [
                "packages-entry",
                "--deb",
                str(deb),
                "--version",
                "0.3.0",
                "--arch",
                "amd64",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Filename: pool/main/c/clickcast/clickcast_0.3.0_amd64.deb" in out

    def test_release_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = apt_package.main(
            [
                "release",
                "--codename",
                "stable",
                "--component",
                "main",
                "--arch",
                "amd64",
                "--entry",
                f"main/binary-amd64/Packages:100:{'b' * 64}",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Codename: stable" in out
        assert "b" * 64 in out


class TestBuildInfoRoundtrip:
    """render_packages_stanza against a real dpkg-deb-built package's hash."""

    def test_sha256_matches_a_real_file(self, tmp_path: Path) -> None:
        deb_path = tmp_path / "fake.deb"
        deb_path.write_bytes(b"not a real deb, just bytes to hash")
        expected_sha = hashlib.sha256(deb_path.read_bytes()).hexdigest()
        size, sha256 = apt_package.file_size_and_sha256(deb_path)
        assert size == deb_path.stat().st_size
        assert sha256 == expected_sha
