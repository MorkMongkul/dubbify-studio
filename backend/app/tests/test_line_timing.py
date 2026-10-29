"""Where each line starts: Movelo's pause-based start, moved to where the line's first character is heard when
the two are far apart (app.services.line_timing). No test runs the speech model or downloads it."""
import asyncio
import bz2
import hashlib
import io
import tarfile

import httpx
import pytest

from app.services import line_timing
from app.services.diarizer import DiarizedSegment


def heard(text: str, start: float, step: float = 0.25) -> list[tuple[str, float]]:
    """The model hearing `text` one character every `step` seconds from `start`."""
    return [(ch, start + k * step) for k, ch in enumerate(text)]


def test_a_line_starts_just_before_its_first_character_when_far_from_it():
    # Movelo's stretch began 1.5 s early (a breath, someone else's word): the line starts 0.1 s before 你
    assert line_timing.refined_spans([(10.0, 14.0, "你好吗我很好")], heard("你好吗我很好", 11.5)) == [(11.4, 14.0)]


def test_a_start_within_a_second_of_the_first_character_stays():
    assert line_timing.MOVE_OVER_SECS == 1.0
    assert line_timing.refined_spans([(10.0, 14.0, "你好吗我很好")], heard("你好吗我很好", 10.8)) == [(10.0, 14.0)]


def test_a_line_whose_words_werent_heard_keeps_its_start():
    assert line_timing.refined_spans([(10.0, 14.0, "你好吗我很好")], heard("完全不同的话", 12.0)) == [(10.0, 14.0)]


def test_one_matching_character_is_not_enough():
    assert line_timing.refined_spans([(10.0, 14.0, "你好吗我很好")], heard("好", 12.0)) == [(10.0, 14.0)]


def test_less_than_half_the_line_heard_is_not_enough():
    text = "这是一句很长很长的台词啊"            # 12 characters, 5 heard
    assert line_timing.refined_spans([(10.0, 15.0, text)], heard("这是一句很", 12.0)) == [(10.0, 15.0)]


def test_traditional_characters_and_punctuation_still_match():
    assert line_timing.refined_spans([(10.0, 14.0, "你們好！我很好。")], heard("你们好我很好", 11.5)) == [(11.4, 14.0)]


def test_lines_of_one_episode_are_matched_in_order():
    lines = [(0.0, 3.0, "你好吗"), (3.0, 6.0, "我很好"), (6.0, 9.0, "再见了")]
    sound = heard("你好吗", 0.2) + heard("我很好", 4.6) + heard("再见了", 6.3)
    # only the middle line was far from its words (its stretch was split by text length)
    assert line_timing.refined_spans(lines, sound) == [(0.0, 3.0), (4.5, 6.0), (6.0, 9.0)]


def test_a_line_moved_past_its_own_end_ends_after_its_last_character():
    [(start, end)] = line_timing.refined_spans([(10.0, 11.0, "你好吗我")], heard("你好吗我", 12.0))
    assert start == pytest.approx(11.9) and end == pytest.approx(12.75 + line_timing.END_PAD_SECS)


def test_a_line_never_starts_before_the_episode():
    assert line_timing.refined_spans([(3.0, 5.0, "你好吗")], heard("你好吗", 0.05))[0][0] == 0.0


def _lines(*spans):
    return [DiarizedSegment("SPEAKER_00", s, e, "man", "adult", text) for s, e, text in spans]


def test_refining_without_the_model_changes_nothing(monkeypatch):
    monkeypatch.setattr(line_timing, "model_ready", lambda: False)
    lines = _lines((10.0, 14.0, "你好吗我很好"))
    assert asyncio.run(line_timing.refine("/vocals.wav", lines, "zh")) == 0
    assert (lines[0].start_time, lines[0].end_time) == (10.0, 14.0)


def test_refining_moves_the_lines_far_from_their_words(monkeypatch):
    monkeypatch.setattr(line_timing, "model_ready", lambda: True)
    monkeypatch.setattr(line_timing, "heard_characters", lambda vocals: heard("你好吗我很好", 11.5))
    lines = _lines((10.0, 14.0, "你好吗我很好"), (20.0, 21.0, ""))
    assert asyncio.run(line_timing.refine("/vocals.wav", lines, "zh")) == 1
    assert (lines[0].start_time, lines[0].end_time) == (pytest.approx(11.4), 14.0)
    assert (lines[1].start_time, lines[1].end_time) == (20.0, 21.0)


def test_refining_only_chinese(monkeypatch):
    monkeypatch.setattr(line_timing, "model_ready", lambda: True)
    monkeypatch.setattr(line_timing, "heard_characters", lambda vocals: pytest.fail("the model ran"))
    assert asyncio.run(line_timing.refine("/vocals.wav", _lines((10.0, 14.0, "hello")), "en")) == 0


def test_refining_never_fails_the_analysis(monkeypatch):
    def broken(vocals):
        raise RuntimeError("the model crashed")
    monkeypatch.setattr(line_timing, "model_ready", lambda: True)
    monkeypatch.setattr(line_timing, "heard_characters", broken)
    lines = _lines((10.0, 14.0, "你好吗我很好"))
    assert asyncio.run(line_timing.refine("/vocals.wav", lines, "zh")) == 0
    assert lines[0].start_time == 10.0


def _archive() -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name, data in ((f"{line_timing.ARCHIVE_DIR}/model.int8.onnx", b"onnx"),
                           (f"{line_timing.ARCHIVE_DIR}/tokens.txt", b"tokens"),
                           (f"{line_timing.ARCHIVE_DIR}/test_wavs/zh.wav", b"wav")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return bz2.compress(raw.getvalue())


def _serve(monkeypatch, body: bytes):
    real = httpx.AsyncClient

    def client(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)), **kwargs)
    monkeypatch.setattr(line_timing.httpx, "AsyncClient", client)


def test_the_model_is_downloaded_checked_and_unpacked_once(monkeypatch, tmp_path):
    body = _archive()
    monkeypatch.setattr(line_timing.settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(line_timing, "ARCHIVE_SHA256", hashlib.sha256(body).hexdigest())
    _serve(monkeypatch, body)
    assert asyncio.run(line_timing.ensure_model()) is True
    folder = line_timing.model_dir()
    assert (folder / "model.int8.onnx").read_bytes() == b"onnx" and (folder / "tokens.txt").read_bytes() == b"tokens"
    assert sorted(p.name for p in folder.iterdir()) == ["model.int8.onnx", "tokens.txt"]   # nothing else unpacked
    assert line_timing.model_ready()
    _serve(monkeypatch, b"not asked for")
    assert asyncio.run(line_timing.ensure_model()) is True


def test_a_download_with_the_wrong_checksum_is_thrown_away(monkeypatch, tmp_path):
    monkeypatch.setattr(line_timing.settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(line_timing, "ARCHIVE_SHA256", "0" * 64)
    _serve(monkeypatch, _archive())
    assert asyncio.run(line_timing.ensure_model()) is False
    assert not line_timing.model_ready()
    assert not any(p.suffix in (".part", ".bz2") for p in tmp_path.rglob("*"))


def test_without_a_data_folder_nothing_is_downloaded(monkeypatch):
    monkeypatch.setattr(line_timing.settings, "DUBIFY_DATA_DIR", "")
    _serve(monkeypatch, b"never")
    assert line_timing.model_dir() is None
    assert asyncio.run(line_timing.ensure_model()) is False
    assert not line_timing.model_ready()


def test_a_mac_that_cannot_run_the_model_downloads_nothing(monkeypatch, tmp_path):
    from app.services.characters import models
    monkeypatch.setattr(line_timing.settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "runtime_supported", lambda: False)
    asked = []
    real = httpx.AsyncClient
    monkeypatch.setattr(line_timing.httpx, "AsyncClient",
                        lambda **kw: asked.append(1) or real(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert asyncio.run(line_timing.ensure_model()) is False
    assert asked == []
