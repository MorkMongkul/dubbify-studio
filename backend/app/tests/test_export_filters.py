"""
The export must work with the LGPL ffmpeg bundled in the desktop app
(scripts/build-ffmpeg.sh), which leaves out FFmpeg's GPL-only filters and
the x264 encoder. The ffmpeg command is captured instead of run.
"""
import pytest

from app.services import audio_extractor as ax

BLUR_BOX = {
    "kind": "shape", "x": 0.6, "y": 0.4, "width": 0.3, "height": 0.2,
    "color": "black", "opacity": 1.0, "blur": True, "start_time": None, "end_time": None,
}


class _Proc:
    def __init__(self, returncode: int, stderr: bytes):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return b"", self._stderr


def _capture_ffmpeg(monkeypatch, results=()):
    """Record each ffmpeg command; answer with the given (returncode, stderr) pairs, then success."""
    commands, pending = [], list(results)

    async def fake_exec(*cmd, **kwargs):
        commands.append(list(cmd))
        returncode, stderr = pending.pop(0) if pending else (0, b"")
        return _Proc(returncode, stderr)

    monkeypatch.setattr(ax.asyncio, "create_subprocess_exec", fake_exec)
    return commands


async def _export_with_blur_box(tmp_path):
    clip = tmp_path / "line.wav"
    clip.write_bytes(b"RIFF")
    return await ax.mix_dubbed_audio(
        str(tmp_path / "in.mp4"), [{"start_time": 0.0, "audio_path": str(clip), "duration": 1.0}],
        str(tmp_path / "out.mp4"), video_layers=[BLUR_BOX], video_width=640, video_height=360,
    )


@pytest.mark.asyncio
async def test_blur_boxes_use_a_filter_the_bundled_lgpl_ffmpeg_has(monkeypatch, tmp_path):
    """boxblur is GPL-only: with the bundled ffmpeg every export containing a blur box failed."""
    commands = _capture_ffmpeg(monkeypatch)
    await _export_with_blur_box(tmp_path)
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "boxblur" not in graph
    assert "gblur" in graph


@pytest.mark.asyncio
async def test_a_failed_hardware_encode_reports_its_own_error(monkeypatch, tmp_path):
    """Without x264 the fallback can only say 'Unrecognized option preset' — the real cause must surface."""
    monkeypatch.setattr(ax.sys, "platform", "darwin")
    _capture_ffmpeg(monkeypatch, [
        (1, b"Error: cannot create compression session"),
        (1, b"Unrecognized option 'preset'."),
    ])
    with pytest.raises(RuntimeError, match="cannot create compression session"):
        await _export_with_blur_box(tmp_path)
