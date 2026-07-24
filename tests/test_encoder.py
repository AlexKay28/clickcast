from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from clickcast.encode import EncodeResult, encode
from clickcast.encode.encoder import _infer_format, _load_frames


def _make_varied_frame(i: int, size: tuple[int, int]) -> Image.Image:
    """Frame with a rectangle at a distinct position so frames don't dedupe."""
    img = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(img)
    x = 10 + i * 15
    draw.rectangle([x, 30, x + 40, 100], fill=(200 - i * 10, 50 + i * 10, 100))
    draw.rectangle([0, size[1] - 10, size[0], size[1]], fill=(i * 25, 0, 0))
    return img


@pytest.fixture
def frames_dir(tmp_path: Path) -> Path:
    d = tmp_path / "frames"
    d.mkdir()
    for i in range(10):
        _make_varied_frame(i, (200, 150)).save(d / f"frame-0000-{i:03d}.png")
    manifest = {
        "fps": 5,
        "count": 10,
        "frames": [
            {
                "path": f"frame-0000-{i:03d}.png",
                "step_index": 0,
                "sub_index": i,
                "cursor_xy": None,
            }
            for i in range(10)
        ],
    }
    (d / "frames.json").write_text(json.dumps(manifest))
    return d


@pytest.fixture
def frames_dir_no_manifest(tmp_path: Path) -> Path:
    d = tmp_path / "frames"
    d.mkdir()
    for i in range(5):
        _make_varied_frame(i, (100, 80)).save(d / f"frame-0000-{i:03d}.png")
    return d


class TestFormatInference:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("out.gif", "gif"),
            ("out.mp4", "mp4"),
            ("out.webp", "webp"),
            ("OUT.GIF", "gif"),
            ("frames_dir", "frames"),
        ],
    )
    def test_infer(self, filename: str, expected: str) -> None:
        assert _infer_format(Path(filename)) == expected

    def test_unknown_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot infer"):
            _infer_format(Path("out.xyz"))


class TestLoadFrames:
    def test_uses_manifest_when_present(self, frames_dir: Path) -> None:
        frames = _load_frames(frames_dir)
        assert len(frames) == 10
        assert frames[0].name == "frame-0000-000.png"
        assert frames[-1].name == "frame-0000-009.png"

    def test_falls_back_to_glob(self, frames_dir_no_manifest: Path) -> None:
        frames = _load_frames(frames_dir_no_manifest)
        assert len(frames) == 5
        assert [f.name for f in frames] == sorted(f.name for f in frames)


class TestValidation:
    def test_bad_fps(self, frames_dir: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="fps"):
            encode(frames_dir, tmp_path / "out.gif", fps=0)

    def test_bad_quality(self, frames_dir: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="quality"):
            encode(frames_dir, tmp_path / "out.gif", quality=0)
        with pytest.raises(ValueError, match="quality"):
            encode(frames_dir, tmp_path / "out.gif", quality=31)

    def test_missing_frames_dir(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            encode(tmp_path / "nope", tmp_path / "out.gif")

    def test_empty_frames_dir(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(ValueError, match="No frames"):
            encode(d, tmp_path / "out.gif")

    def test_unknown_format_by_extension(self, frames_dir: Path, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Cannot infer"):
            encode(frames_dir, tmp_path / "out.xyz")


class TestEncoding:
    def test_gif(self, frames_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "tour.gif"
        result = encode(frames_dir, out, fps=5)
        assert isinstance(result, EncodeResult)
        assert result.format == "gif"
        assert result.path == out
        assert result.frame_count == 10
        assert result.duration_s == pytest.approx(2.0)
        assert out.exists()
        assert result.size_bytes == out.stat().st_size
        # Reopen and verify frame count
        with Image.open(out) as img:
            assert img.n_frames == 10

    def test_webp(self, frames_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "tour.webp"
        result = encode(frames_dir, out, fps=5)
        assert result.format == "webp"
        assert out.exists()
        with Image.open(out) as img:
            assert img.n_frames == 10

    def test_mp4(self, frames_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "tour.mp4"
        result = encode(frames_dir, out, fps=5)
        assert result.format == "mp4"
        assert result.frame_count == 10
        assert result.duration_s == pytest.approx(2.0)
        assert out.exists()
        # MP4 header is at least a few hundred bytes even for tiny videos
        assert result.size_bytes > 500

    def test_frames_dir_copy(self, frames_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "copied"
        result = encode(frames_dir, out)
        assert result.format == "frames"
        assert out.is_dir()
        assert len(list(out.glob("*.png"))) == 10
        assert (out / "frames.json").exists()

    def test_frames_no_copy_when_source_equals_dest(self, frames_dir: Path) -> None:
        # Should not blow up when out_path already IS the frames_dir
        result = encode(frames_dir, frames_dir, format="frames")
        assert result.format == "frames"
        assert result.frame_count == 10

    def test_format_kwarg_overrides_extension(self, frames_dir: Path, tmp_path: Path) -> None:
        # File has no extension → would infer "frames", but we force "gif"
        out = tmp_path / "no_ext"
        result = encode(frames_dir, out, fps=5, format="gif")
        assert result.format == "gif"
        with Image.open(out) as img:
            assert img.n_frames == 10

    def test_quality_affects_mp4_size(self, frames_dir: Path, tmp_path: Path) -> None:
        # Higher quality number = worse quality = smaller file
        hi = encode(frames_dir, tmp_path / "hi.mp4", fps=5, quality=1)
        lo = encode(frames_dir, tmp_path / "lo.mp4", fps=5, quality=30)
        # Not a strict guarantee at 10 frames, but expected on this fixture
        assert lo.size_bytes <= hi.size_bytes

    def test_loop_kwarg_written_to_gif(self, frames_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "loop.gif"
        encode(frames_dir, out, fps=5, loop=3)
        with Image.open(out) as img:
            # Pillow exposes loop via info["loop"] on animated GIFs
            assert img.info.get("loop") == 3


class TestEncodeResultShape:
    def test_all_fields(self, frames_dir: Path, tmp_path: Path) -> None:
        result = encode(frames_dir, tmp_path / "out.gif", fps=10)
        assert result.frame_count == 10
        assert result.duration_s == pytest.approx(1.0)
        assert result.size_bytes > 0
        assert result.format == "gif"
        assert result.path == tmp_path / "out.gif"


class TestGifPaletteRegression:
    """Regression: a leading blank frame must not poison the animation palette.

    The demo pipeline captures an `about:blank` pre-goto frame followed by
    real dark-background content. Sampling only the first frame for palette
    quantized all subsequent frames to white and Pillow's optimizer
    collapsed the animation to a single 1 KB frame.
    """

    def test_blank_first_frame_does_not_collapse_animation(self, tmp_path: Path) -> None:
        d = tmp_path / "frames"
        d.mkdir()
        # Frame 0: all-white blank (mimics about:blank pre-goto capture)
        Image.new("RGB", (200, 150), color="white").save(d / "frame-0000-000.png")
        # Frames 1..9: dark background with a moving coloured marker
        for i in range(1, 10):
            img = Image.new("RGB", (200, 150), color=(10, 14, 20))  # worldsight bg
            draw = ImageDraw.Draw(img)
            draw.rectangle([i * 15, 30, i * 15 + 40, 100], fill=(220, 100, 80))
            img.save(d / f"frame-0000-{i:03d}.png")

        result = encode(d, tmp_path / "out.gif", fps=5)
        assert result.frame_count == 10
        with Image.open(tmp_path / "out.gif") as img:
            # If the palette was poisoned by frame 0 all subsequent dark
            # frames would collapse into it and n_frames drops to 1.
            assert img.n_frames == 10
            # Also sanity-check that at least one non-first frame carries
            # dark pixels — the whole point of the fix.
            img.seek(5)
            rgb = img.convert("RGB")
            pixels = rgb.getcolors(maxcolors=100_000) or []
            dark = [c for c in pixels if sum(c[1]) < 200]
            assert dark, "expected dark pixels in a mid-animation frame"


class TestFileHandleHygiene:
    """Regression: `_encode_webp` and `_sample_frames_for_palette` used to leak
    the file handle from `Image.open()` — `.convert()` returns a new image but
    the source handle was dropped without `.close()`. On CPython refcounting
    saved us on short reels; on PyPy or long reels this hit the ulimit.
    """

    def _make_many_frames(self, tmp_path: Path, n: int) -> Path:
        d = tmp_path / "frames"
        d.mkdir()
        for i in range(n):
            _make_varied_frame(i, (60, 40)).save(d / f"frame-0000-{i:03d}.png")
        return d

    def test_gif_encode_does_not_hold_open_source_handles(self, tmp_path: Path) -> None:
        # 40 frames at 60x40 px; encode + immediately remove the source dir.
        # If PIL is still holding descriptors on the source files, this will
        # fail on Windows (locked) or leave zombie handles on POSIX.
        src = self._make_many_frames(tmp_path, 40)
        encode(src, tmp_path / "out.gif", fps=8)
        # After encode returns, every source frame should be closable.
        # We can't easily observe fd counts portably, so we assert the
        # secondary contract: unlink succeeds cleanly.
        for f in src.iterdir():
            f.unlink()

    def test_webp_encode_does_not_hold_open_source_handles(self, tmp_path: Path) -> None:
        src = self._make_many_frames(tmp_path, 40)
        encode(src, tmp_path / "out.webp", fps=8)
        for f in src.iterdir():
            f.unlink()
