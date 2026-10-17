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


class _Stream:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _Proc:
    def __init__(self, returncode: int, stderr: bytes, stdout: bytes = b""):
        self.returncode = returncode
        self._stderr = stderr
        self._stdout = stdout
        # What the progress-reporting path reads instead of communicate()
        self.stdout, self.stderr = _Stream(stdout), _Stream(stderr)

    async def communicate(self):
        return self._stdout, self._stderr

    async def wait(self):
        return self.returncode


def _capture_ffmpeg(monkeypatch, video_encode_results=(), encoders=b"", duration=None):
    """
    Record each ffmpeg command. Video encodes are answered with the given
    (returncode, stderr) pairs, then success; everything else succeeds.
    `encoders` is what `ffmpeg -encoders` lists, `duration` what ffprobe reports.
    """
    commands, pending = [], list(video_encode_results)

    async def fake_exec(*cmd, **kwargs):
        commands.append(list(cmd))
        if "-c:v" in cmd and pending:
            return _Proc(*pending.pop(0))
        if "-encoders" in cmd:
            return _Proc(0, b"", encoders)
        if "-show_format" in cmd and duration:
            return _Proc(0, b"", b'{"format": {"duration": "%f"}}' % duration)
        return _Proc(0, b"")

    monkeypatch.setattr(ax.asyncio, "create_subprocess_exec", fake_exec)
    return commands


VIDEOTOOLBOX_FAILURE = (
    b"[h264_videotoolbox @ 0x1] Error: cannot create compression session\n"
    b"[vost#0:0/h264_videotoolbox @ 0x2] Error while opening encoder - maybe incorrect parameters\n"
    b"Conversion failed!\n"
)


def _final_encode(commands):
    return next(c for c in reversed(commands) if "-c:v" in c)


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
    final = _final_encode(commands)
    graph = final[final.index("-filter_complex") + 1]
    assert "boxblur" not in graph
    assert "gblur" in graph


@pytest.mark.asyncio
async def test_a_failed_hardware_encode_reports_its_own_error(monkeypatch, tmp_path):
    """The bundled ffmpeg has no x264: trying it only produced 'Unrecognized option preset'."""
    monkeypatch.setattr(ax.sys, "platform", "darwin")
    commands = _capture_ffmpeg(monkeypatch, [(1, VIDEOTOOLBOX_FAILURE)])
    with pytest.raises(RuntimeError) as failure:
        await _export_with_blur_box(tmp_path)
    # One short reason for the person exporting, not ffmpeg's whole log.
    assert str(failure.value) == "Error: cannot create compression session"
    assert not any("libx264" in c for c in commands)


@pytest.mark.asyncio
async def test_an_ffmpeg_with_x264_falls_back_to_it(monkeypatch, tmp_path):
    monkeypatch.setattr(ax.sys, "platform", "darwin")
    commands = _capture_ffmpeg(monkeypatch, [(1, VIDEOTOOLBOX_FAILURE)], encoders=(
        b" V....D h264_videotoolbox    VideoToolbox H.264 Encoder (codec h264)\n"
        b" V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)\n"
    ))
    await _export_with_blur_box(tmp_path)
    assert "libx264" in _final_encode(commands)


@pytest.mark.asyncio
async def test_the_voice_track_is_padded_to_the_video_length_not_forever(monkeypatch, tmp_path):
    """
    An endless apad keeps generating silence after the video ends; ffmpeg 8
    sometimes queued it faster than -shortest stopped it, until its queue
    overflowed and the export failed with "No space left on device".
    """
    commands = _capture_ffmpeg(monkeypatch, duration=465.666667)
    await _export_with_blur_box(tmp_path)
    final = _final_encode(commands)
    graph = final[final.index("-filter_complex") + 1]
    assert "apad=whole_dur=465.666667" in graph


def test_the_reason_is_the_first_real_error_line():
    stderr = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'in.mp4':\n"
        "[h264_videotoolbox @ 0x942c74700] Color range not set for yuv420p. Using MPEG range.\n"
        "[fc#0 @ 0x942c34000] Error sending frames to consumers: No space left on device\n"
        "[fc#0 @ 0x942c34000] Task finished with error code: -28 (No space left on device)\n"
        "[out#0/mp4 @ 0x942c483c0] video:9198KiB audio:10913KiB subtitle:0KiB muxing overhead: 1.33%\n"
        "frame=11176 fps=372 q=-0.0 Lsize=   20379KiB time=00:07:45.62 bitrate= 358.5kbits/s\n"
        "Conversion failed!\n"
    )
    assert ax.ffmpeg_failure_reason(stderr) == "Error sending frames to consumers: No space left on device"
    assert ax.ffmpeg_failure_reason("") == "ffmpeg stopped without saying why"
