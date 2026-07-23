from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from clickcast.capture import FrameRef, Recorder
from clickcast.core.actions import ClickStep, GotoStep, execute
from clickcast.core.session import Session

FIXTURE_HTML = """<!DOCTYPE html>
<html><body>
  <button id="btn1">One</button>
  <button id="btn2">Two</button>
</body></html>
"""


class TestRecorderConfig:
    def test_defaults(self) -> None:
        rec = Recorder()
        assert rec.fps == 12
        assert rec.default_dwell == 1.0
        assert rec.keep is False
        assert rec.out_dir == Path("frames")

    def test_out_dir_accepts_str(self) -> None:
        rec = Recorder(out_dir="my/frames")
        assert rec.out_dir == Path("my/frames")

    def test_rejects_bad_fps(self) -> None:
        with pytest.raises(ValueError, match="fps"):
            Recorder(fps=0)

    def test_rejects_negative_dwell(self) -> None:
        with pytest.raises(ValueError, match="dwell"):
            Recorder(default_dwell=-1.0)

    def test_frames_dir_before_enter_raises(self) -> None:
        rec = Recorder()
        with pytest.raises(RuntimeError, match="not open"):
            _ = rec.frames_dir

    def test_flush_before_enter_raises(self) -> None:
        rec = Recorder()
        with pytest.raises(RuntimeError, match="not open"):
            rec.flush()


class TestFrameFilenames:
    def test_pattern_pads_indices(self, tmp_path: Path) -> None:
        rec = Recorder()
        rec._tmp_path = tmp_path
        rec._entered = True
        rec._step_index = 3
        rec._sub_index = 7
        assert rec._next_path() == tmp_path / "frame-0003-007.png"

    def test_pattern_at_start(self, tmp_path: Path) -> None:
        rec = Recorder()
        rec._tmp_path = tmp_path
        rec._entered = True
        assert rec._next_path() == tmp_path / "frame-0000-000.png"


@pytest_asyncio.fixture
async def loaded_session() -> AsyncIterator[Session]:
    async with Session(viewport=(320, 240)) as sess:
        await sess.page.set_content(FIXTURE_HTML)
        sess.page.set_default_timeout(3000)
        yield sess


@pytest.mark.integration
class TestRecorderIntegration:
    async def test_single_step_yields_1_pre_plus_N_padding(self, loaded_session: Session) -> None:
        with Recorder(fps=6, default_dwell=1.0) as rec:
            await rec.pre_action(loaded_session)
            result = await execute(ClickStep(selector="#btn1"), loaded_session)
            paths = await rec.post_action(loaded_session, result, ClickStep(selector="#btn1"))

            # 1 pre-frame + round(1.0 * 6) = 6 post-padding frames
            assert len(rec.frames) == 1 + 6
            assert len(paths) == 6
            assert all(p.exists() for p in paths)
            # deterministic filenames, no gaps within step 0
            names = [f.path.name for f in rec.frames]
            assert names == [
                "frame-0000-000.png",
                "frame-0000-001.png",
                "frame-0000-002.png",
                "frame-0000-003.png",
                "frame-0000-004.png",
                "frame-0000-005.png",
                "frame-0000-006.png",
            ]

    async def test_multi_step_indices_increment(self, loaded_session: Session) -> None:
        with Recorder(fps=4, default_dwell=0.5) as rec:  # 2 padding per step
            for step in (
                ClickStep(selector="#btn1"),
                ClickStep(selector="#btn2"),
                ClickStep(selector="#btn1"),
            ):
                await rec.pre_action(loaded_session)
                result = await execute(step, loaded_session)
                await rec.post_action(loaded_session, result, step)

            # Each step: 1 pre + round(0.5 * 4) = 2 post = 3 frames per step
            step_indices = sorted({f.step_index for f in rec.frames})
            assert step_indices == [0, 1, 2]
            counts = [sum(1 for f in rec.frames if f.step_index == i) for i in step_indices]
            assert counts == [3, 3, 3]

            # Sub-index resets per step and increments contiguously
            for i in step_indices:
                subs = [f.sub_index for f in rec.frames if f.step_index == i]
                assert subs == list(range(len(subs)))

    async def test_flush_writes_manifest(self, loaded_session: Session) -> None:
        with Recorder(fps=6, default_dwell=0.5) as rec:  # 3 post per step
            await rec.pre_action(loaded_session)
            result = await execute(ClickStep(selector="#btn1"), loaded_session)
            await rec.post_action(loaded_session, result, ClickStep(selector="#btn1"))

            paths = rec.flush()
            assert paths == [f.path for f in rec.frames]

            manifest_path = rec.frames_dir / "frames.json"
            manifest = json.loads(manifest_path.read_text())
            assert manifest["fps"] == 6
            assert manifest["count"] == 1 + 3
            assert len(manifest["frames"]) == 1 + 3
            # First is the pre-frame with no cursor
            assert manifest["frames"][0]["step_index"] == 0
            assert manifest["frames"][0]["sub_index"] == 0
            assert manifest["frames"][0]["cursor_xy"] is None
            # Post frames share the cursor position from ActionResult
            for entry in manifest["frames"][1:]:
                assert entry["cursor_xy"] is not None
                assert entry["step_index"] == 0

    async def test_keep_true_copies_to_out_dir(
        self, loaded_session: Session, tmp_path: Path
    ) -> None:
        out = tmp_path / "kept"
        with Recorder(fps=4, default_dwell=0.25, keep=True, out_dir=out) as rec:
            await rec.pre_action(loaded_session)
            result = await execute(ClickStep(selector="#btn1"), loaded_session)
            await rec.post_action(loaded_session, result, ClickStep(selector="#btn1"))
            rec.flush()

        # After __exit__, out_dir has all frames + the manifest
        assert out.exists()
        pngs = sorted(out.glob("*.png"))
        assert len(pngs) == 1 + 1  # 1 pre + round(0.25*4) = 1 post
        assert (out / "frames.json").exists()

    async def test_keep_false_cleans_tmp(self, loaded_session: Session) -> None:
        rec = Recorder(fps=4, default_dwell=0.25)
        with rec:
            await rec.pre_action(loaded_session)
            result = await execute(ClickStep(selector="#btn1"), loaded_session)
            await rec.post_action(loaded_session, result, ClickStep(selector="#btn1"))
            tmp = rec.frames_dir
            assert tmp.exists()
        # After exit, tmp is gone
        assert not tmp.exists()

    async def test_padding_frames_share_bytes_with_first_post(
        self, loaded_session: Session
    ) -> None:
        with Recorder(fps=4, default_dwell=0.5) as rec:  # 2 post per step
            await rec.pre_action(loaded_session)
            result = await execute(ClickStep(selector="#btn1"), loaded_session)
            paths = await rec.post_action(loaded_session, result, ClickStep(selector="#btn1"))
            # first == first post frame; the copy should be byte-identical
            assert paths[0].read_bytes() == paths[1].read_bytes()

    async def test_roadmap_arithmetic_10_steps_at_12fps(self, loaded_session: Session) -> None:
        # From the acceptance criteria in issue #5: ~120 frames for a 10-step
        # scenario at fps=12, dwell=1.0. With our "1 pre + N post" contract:
        # 10 * (1 + 12) = 130.
        step = GotoStep(url="data:text/html,<p>x</p>", wait="load")
        with Recorder(fps=12, default_dwell=1.0) as rec:
            for _ in range(10):
                await rec.pre_action(loaded_session)
                result = await execute(step, loaded_session)
                await rec.post_action(loaded_session, result, step)
            assert len(rec.frames) == 130

    async def test_flush_paths_are_ordered(self, loaded_session: Session) -> None:
        with Recorder(fps=4, default_dwell=0.25) as rec:
            for step in (
                ClickStep(selector="#btn1"),
                ClickStep(selector="#btn2"),
            ):
                await rec.pre_action(loaded_session)
                result = await execute(step, loaded_session)
                await rec.post_action(loaded_session, result, step)
            paths = rec.flush()
            names = [p.name for p in paths]
            assert names == sorted(names)  # deterministic filenames sort correctly


class TestFrameRefIsFrozen:
    def test_frame_ref_is_hashable(self) -> None:
        f = FrameRef(path=Path("x.png"), step_index=0, sub_index=0, cursor_xy=None)
        assert hash(f) == hash(f)
