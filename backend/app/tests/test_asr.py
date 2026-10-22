"""
Tests for app.services.asr.

Pure logic (silence parsing, span merging, window planning, request building,
response parsing, speaker-label resolution, turn splitting) is tested
directly. The orchestration (transcribe_audio / label_speech) runs with the
Gemini call and the ffmpeg slicer replaced by fakes, so the tests pin down
how results are stitched together without network or audio files. The HTTP
retry policy runs against httpx.MockTransport.
"""
import asyncio
import json

import httpx
import pytest

from app.services import asr
from app.services.asr import (
    SpeakerReference,
    _build_window_parts,
    _invert_to_speech_spans,
    _merge_and_pad,
    _parse_silencedetect_log,
    _parse_window_rows,
    _plan_windows,
    _resolve_speakers,
    _split_turns,
    _LabelAllocator,
    MAX_MERGED_DURATION,
    MERGE_GAP_THRESHOLD,
    MIN_SPEECH_DURATION,
)


# ── Silence detection → speech spans (unchanged behaviour) ────────────────────

def test_parse_silencedetect_log_extracts_start_end_pairs():
    log = """
[silencedetect @ 0x1] silence_start: 2.5
[silencedetect @ 0x1] silence_end: 4.1 | silence_duration: 1.6
some unrelated ffmpeg noise
[silencedetect @ 0x1] silence_start: 10.0
[silencedetect @ 0x1] silence_end: 10.9 | silence_duration: 0.9
"""
    result = _parse_silencedetect_log(log)
    assert result == [(2.5, 4.1), (10.0, 10.9)]


def test_parse_silencedetect_log_ignores_unterminated_trailing_silence():
    """A silence_start with no matching silence_end (e.g. clip ends mid-silence) is dropped."""
    log = "silence_start: 5.0\nsilence_start: 20.0\nsilence_end: 21.0"
    assert _parse_silencedetect_log(log) == [(20.0, 21.0)]


def test_invert_to_speech_spans_basic():
    # 10s clip, silence from 2-4 and 6-7 → speech at [0-2], [4-6], [7-10]
    silences = [(2.0, 4.0), (6.0, 7.0)]
    spans = _invert_to_speech_spans(silences, duration=10.0)
    assert spans == [(0.0, 2.0), (4.0, 6.0), (7.0, 10.0)]


def test_invert_to_speech_spans_drops_short_blips():
    tiny_gap = MIN_SPEECH_DURATION / 2
    silences = [(2.0, 4.0), (4.0 + tiny_gap, 8.0)]
    spans = _invert_to_speech_spans(silences, duration=10.0)
    assert all(e - s >= MIN_SPEECH_DURATION for s, e in spans)
    assert (4.0, 4.0 + tiny_gap) not in spans


def test_invert_to_speech_spans_no_silence_returns_whole_clip():
    assert _invert_to_speech_spans([], duration=5.0) == [(0.0, 5.0)]


def test_merge_and_pad_merges_across_short_gap():
    gap = MERGE_GAP_THRESHOLD / 2
    spans = [(1.0, 3.0), (3.0 + gap, 5.0)]
    result = _merge_and_pad(spans, duration=20.0)
    assert len(result) == 1
    start, end = result[0]
    assert start <= 1.0 and end >= 5.0


def test_merge_and_pad_does_not_merge_beyond_max_duration():
    spans = [(0.0, MAX_MERGED_DURATION), (MAX_MERGED_DURATION + 0.1, MAX_MERGED_DURATION + 5)]
    result = _merge_and_pad(spans, duration=100.0)
    assert len(result) == 2


def test_merge_and_pad_empty_input():
    assert _merge_and_pad([], duration=10.0) == []


# ── Slicing ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_slice_chunk_seeks_before_opening_input(monkeypatch):
    """-ss before -i makes ffmpeg jump straight to the clip instead of decoding
    the whole file from the start for every clip (quadratic on long videos)."""
    captured = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(asr.asyncio, "create_subprocess_exec", fake_exec)
    await asr._slice_chunk("in.wav", 12.5, 14.0, "out.wav")
    cmd = captured["cmd"]
    assert cmd.index("-ss") < cmd.index("-i")


# ── Window planning ───────────────────────────────────────────────────────────

def test_plan_windows_keeps_a_short_scene_in_one_window():
    spans = [(i * 2.0, i * 2.0 + 1.5) for i in range(10)]
    assert _plan_windows(spans, max_clips=40) == [list(range(10))]


def test_plan_windows_never_exceeds_max_and_covers_every_clip_in_order():
    spans = [(i * 2.0, i * 2.0 + 1.5) for i in range(95)]
    windows = _plan_windows(spans, max_clips=40)
    assert all(1 <= len(w) <= 40 for w in windows)
    assert [i for w in windows for i in w] == list(range(95))


def test_plan_windows_limits_total_audio_seconds_per_request():
    """Gemini rejects inline-audio requests over ~20 MB; 40 long clips would exceed it."""
    spans = [(i * 13.0, i * 13.0 + 12.0) for i in range(40)]
    windows = _plan_windows(spans, max_clips=40, max_seconds=100.0)
    assert all(sum(spans[i][1] - spans[i][0] for i in w) <= 100.0 for w in windows)
    assert [i for w in windows for i in w] == list(range(40))


def test_plan_windows_cuts_at_the_widest_pause_near_the_limit():
    # Clips every 2 s, except a 6 s pause after clip 35 (a scene break).
    spans = []
    t = 0.0
    for i in range(50):
        spans.append((t, t + 1.5))
        t += 2.0 if i != 35 else 8.0
    windows = _plan_windows(spans, max_clips=40)
    assert windows[0] == list(range(36))
    assert windows[1][0] == 36


# ── Request building ──────────────────────────────────────────────────────────

def _write(tmp_path, name, data=b"RIFFfake"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_build_window_parts_labels_every_clip_with_its_number_and_duration(tmp_path):
    clips = [(_write(tmp_path, "a.wav"), 1.24), (_write(tmp_path, "b.wav"), 9.4)]
    parts = _build_window_parts(clips, refs=[])
    audio_idx = [i for i, p in enumerate(parts) if "inlineData" in p]
    assert len(audio_idx) == 2
    assert parts[audio_idx[0] - 1]["text"] == "Clip 1 (1.2 s):"
    assert parts[audio_idx[1] - 1]["text"] == "Clip 2 (9.4 s):"
    assert "2 consecutive audio clips" in parts[0]["text"]


def test_build_window_parts_puts_reference_voices_first_with_ids_and_transcripts(tmp_path):
    ref = SpeakerReference(label="SPEAKER_03", clip_path=_write(tmp_path, "r.wav"), text="我已经有工作了。", duration=2.0)
    clips = [(_write(tmp_path, "a.wav"), 1.0)]
    parts = _build_window_parts(clips, refs=[("R1", ref)])
    texts = [p.get("text", "") for p in parts]
    ref_pos = next(i for i, t in enumerate(texts) if t.startswith("Reference voice R1"))
    clip_pos = next(i for i, t in enumerate(texts) if t.startswith("Clip 1"))
    assert ref_pos < clip_pos
    assert "我已经有工作了。" in texts[ref_pos]
    assert "inlineData" in parts[ref_pos + 1]
    assert "R1" in parts[0]["text"]          # the instructions explain reference IDs


def test_build_window_parts_asks_for_simplified_characters_for_chinese(tmp_path):
    parts = _build_window_parts([(_write(tmp_path, "a.wav"), 1.0)], refs=[], language="zh")
    assert "SIMPLIFIED" in parts[0]["text"]
    parts_en = _build_window_parts([(_write(tmp_path, "a.wav"), 1.0)], refs=[], language="en")
    assert "English" in parts_en[0]["text"] and "SIMPLIFIED" not in parts_en[0]["text"]


# ── Response parsing ──────────────────────────────────────────────────────────

def test_parse_window_rows_reads_turns_and_no_speech():
    rows = [
        {"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "你好"}]},
        {"clip": 2, "no_speech": True, "turns": []},
        {"clip": 3, "no_speech": False, "turns": [{"speaker": "A", "text": "问"}, {"speaker": "B", "text": "答"}]},
    ]
    parsed = _parse_window_rows(rows, n_clips=3)
    assert parsed == {1: [("A", "你好")], 2: None, 3: [("A", "问"), ("B", "答")]}


def test_parse_window_rows_leaves_missing_clips_out_and_ignores_bad_numbers():
    rows = [
        {"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "一"}]},
        {"clip": 1, "no_speech": False, "turns": [{"speaker": "B", "text": "重复"}]},   # duplicate → first wins
        {"clip": 9, "no_speech": False, "turns": [{"speaker": "A", "text": "越界"}]},   # out of range
        {"clip": "x", "no_speech": False, "turns": []},
    ]
    parsed = _parse_window_rows(rows, n_clips=3)
    assert parsed == {1: [("A", "一")]}


def test_parse_window_rows_treats_no_speech_marker_and_empty_turns_as_no_speech():
    """The old line parser turned '[no speech]' into speaker 'N' with text 'o speech]'."""
    rows = [
        {"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "[no speech]"}]},
        {"clip": 2, "no_speech": False, "turns": [{"speaker": "B", "text": "  "}]},
    ]
    assert _parse_window_rows(rows, n_clips=2) == {1: None, 2: None}


# ── Speaker label resolution ──────────────────────────────────────────────────

def test_resolve_speakers_maps_reference_ids_and_allocates_fresh_labels():
    alloc = _LabelAllocator(taken={"SPEAKER_00", "SPEAKER_01"})
    turns = {1: [("R1", "a")], 2: [("A", "b")], 3: [("A", "c"), ("B", "d")]}
    resolved = _resolve_speakers(turns, ref_ids={"R1": "SPEAKER_01"}, allocator=alloc)
    assert resolved[1] == [("SPEAKER_01", "a")]
    assert resolved[2] == [("SPEAKER_02", "b")]          # first free label, not a taken one
    assert resolved[3] == [("SPEAKER_02", "c"), ("SPEAKER_03", "d")]


def test_resolve_speakers_treats_unknown_reference_ids_as_new_voices():
    alloc = _LabelAllocator(taken=set())
    resolved = _resolve_speakers({1: [("R7", "x")]}, ref_ids={}, allocator=alloc)
    assert resolved[1] == [("SPEAKER_00", "x")]


# ── Turn splitting ────────────────────────────────────────────────────────────

def test_split_turns_divides_a_clip_by_character_share():
    pieces = _split_turns((10.0, 16.0), [("SPEAKER_00", "一二三四"), ("SPEAKER_01", "五六")])
    assert pieces[0][:3] == (10.0, 14.0, "SPEAKER_00")
    assert pieces[1][:3] == (14.0, 16.0, "SPEAKER_01")
    assert pieces[0][3] == "一二三四" and pieces[1][3] == "五六"


def test_split_turns_single_turn_keeps_the_whole_clip():
    assert _split_turns((1.0, 2.5), [("SPEAKER_00", "好")]) == [(1.0, 2.5, "SPEAKER_00", "好")]


# ── Labels for fixed spans by time overlap ────────────────────────────────────

def _piece(label, start, end, text="话"):
    from app.services.diarizer import DiarizedSegment
    return DiarizedSegment(label, start, end, "unknown", "adult", text)


def test_labels_for_spans_picks_the_speaker_who_talks_most_inside_each_span():
    pieces = [_piece("SPEAKER_00", 0.0, 4.0), _piece("SPEAKER_01", 4.0, 5.0)]
    assert asr.labels_for_spans([(1.0, 5.0)], pieces) == ["SPEAKER_00"]   # 3 s vs 1 s


def test_labels_for_spans_uses_the_nearest_speaker_when_nothing_overlaps():
    pieces = [_piece("SPEAKER_00", 0.0, 1.0), _piece("SPEAKER_01", 10.0, 11.0)]
    assert asr.labels_for_spans([(8.0, 9.0)], pieces) == ["SPEAKER_01"]


def test_labels_for_spans_ignores_pieces_without_text():
    """Pieces whose request failed carry a guessed label, not a heard one."""
    pieces = [_piece("SPEAKER_05", 0.0, 10.0, text=""), _piece("SPEAKER_01", 9.0, 12.0)]
    assert asr.labels_for_spans([(0.0, 10.0)], pieces) == ["SPEAKER_01"]


def test_labels_for_spans_without_any_heard_speech_returns_none():
    assert asr.labels_for_spans([(0.0, 1.0)], [_piece("SPEAKER_00", 0.0, 1.0, text="")]) is None


# ── Orchestration (fake Gemini + fake slicer) ─────────────────────────────────

@pytest.fixture
def fake_audio(monkeypatch, tmp_path):
    """Replace ffmpeg slicing with tiny placeholder files; make sure a key is 'configured'."""
    async def fake_slice(audio_path, start, end, out_path):
        with open(out_path, "wb") as f:
            f.write(b"RIFF" + f"{start:.2f}".encode())

    monkeypatch.setattr(asr, "_slice_chunk", fake_slice)
    monkeypatch.setattr(asr.settings, "GEMINI_API_KEY", "test-key")
    return str(tmp_path / "vocals.wav")


def _clip_markers(parts):
    return [p["text"] for p in parts if p.get("text", "").startswith("Clip ")]


def _reference_ids_by_text(parts):
    """{'R1': '你好', ...} from the 'Reference voice R1 (1.0 s): 「你好」' markers."""
    out = {}
    for p in parts:
        t = p.get("text", "")
        if t.startswith("Reference voice "):
            rid = t.split()[2]
            out[rid] = t.split("「", 1)[1].rsplit("」", 1)[0]
    return out


@pytest.mark.asyncio
async def test_transcribe_audio_splits_a_mixed_clip_into_two_speakers(monkeypatch, fake_audio):
    async def fake_spans(path):
        return [(0.0, 2.0), (3.0, 9.0)]

    async def fake_call(parts):
        return [
            {"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "你吃饭了吗"}]},
            {"clip": 2, "no_speech": False, "turns": [{"speaker": "A", "text": "吃了"},
                                                      {"speaker": "B", "text": "我还没吃呢"}]},
        ]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, used_mock = await asr.transcribe_audio(fake_audio)

    assert used_mock is False
    assert [(s.speaker_label, s.source_text) for s in segments] == [
        ("SPEAKER_00", "你吃饭了吗"), ("SPEAKER_00", "吃了"), ("SPEAKER_01", "我还没吃呢"),
    ]
    assert segments[1].start_time == 3.0 and segments[2].end_time == 9.0
    assert segments[1].end_time == segments[2].start_time       # contiguous split


@pytest.mark.asyncio
async def test_clips_are_uploaded_as_flac_to_keep_requests_small(monkeypatch, fake_audio):
    """A whole-scene request of WAV clips was ~7 MB of base64 and timed out on a slow uplink."""
    written = []
    seen_mimes = []

    async def fake_slice(audio_path, start, end, out_path):
        written.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"fLaC")

    async def fake_spans(path):
        return [(0.0, 2.0)]

    async def fake_call(parts):
        seen_mimes.extend(p["inlineData"]["mimeType"] for p in parts if "inlineData" in p)
        return [{"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "好"}]}]

    monkeypatch.setattr(asr, "_slice_chunk", fake_slice)
    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    await asr.transcribe_audio(fake_audio)
    assert all(p.endswith(".flac") for p in written)
    assert seen_mimes == ["audio/flac"]


@pytest.mark.asyncio
async def test_slice_chunk_encodes_flac(monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr(asr.asyncio, "create_subprocess_exec", fake_exec)
    await asr._slice_chunk("in.wav", 1.0, 2.0, "out.flac")
    cmd = captured["cmd"]
    assert cmd[cmd.index("-c:a") + 1] == "flac"


@pytest.mark.asyncio
async def test_transcribe_audio_normalises_chinese_to_simplified_characters(monkeypatch, fake_audio):
    """Gemini drifts into traditional characters even when told not to (seen on a real episode)."""
    async def fake_spans(path):
        return [(0.0, 3.0)]

    async def fake_call(parts):
        return [{"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "這可是兩萬塊錢"}]}]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, _ = await asr.transcribe_audio(fake_audio, language="zh")
    assert segments[0].source_text == "这可是两万块钱"


def test_missing_opencc_skips_normalisation_instead_of_failing(monkeypatch):
    """A machine that hasn't reinstalled requirements must still transcribe — just unnormalised."""
    import sys
    monkeypatch.setattr(asr, "_t2s", None)
    monkeypatch.setitem(sys.modules, "opencc", None)    # makes `import opencc` raise ImportError
    assert asr._to_simplified("這可是兩萬塊錢") == "這可是兩萬塊錢"


@pytest.mark.asyncio
async def test_transcribe_audio_leaves_other_languages_unconverted(monkeypatch, fake_audio):
    async def fake_spans(path):
        return [(0.0, 3.0)]

    async def fake_call(parts):
        return [{"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "Hello 這"}]}]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, _ = await asr.transcribe_audio(fake_audio, language="en")
    assert segments[0].source_text == "Hello 這"


@pytest.mark.asyncio
async def test_transcribe_audio_drops_clips_without_speech(monkeypatch, fake_audio):
    async def fake_spans(path):
        return [(0.0, 2.0), (3.0, 4.0)]

    async def fake_call(parts):
        return [
            {"clip": 1, "no_speech": True, "turns": []},
            {"clip": 2, "no_speech": False, "turns": [{"speaker": "A", "text": "好"}]},
        ]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, _ = await asr.transcribe_audio(fake_audio)
    assert [(s.start_time, s.source_text) for s in segments] == [(3.0, "好")]


@pytest.mark.asyncio
async def test_transcribe_audio_carries_known_voices_into_the_next_window(monkeypatch, fake_audio):
    """Window 2 is offered window 1's voices as references and reuses their labels."""
    monkeypatch.setattr(asr, "ASR_WINDOW_MAX_CLIPS", 2)

    async def fake_spans(path):
        return [(0.0, 2.0), (2.5, 4.5), (5.0, 7.0), (7.5, 9.5)]

    calls = []

    async def fake_call(parts):
        calls.append(parts)
        if len(calls) == 1:
            return [
                {"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "我是老板"}]},
                {"clip": 2, "no_speech": False, "turns": [{"speaker": "B", "text": "我是员工"}]},
            ]
        refs = _reference_ids_by_text(parts)
        boss = next(rid for rid, text in refs.items() if text == "我是老板")
        return [
            {"clip": 1, "no_speech": False, "turns": [{"speaker": boss, "text": "又是我"}]},
            {"clip": 2, "no_speech": False, "turns": [{"speaker": "A", "text": "新来的"}]},
        ]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, _ = await asr.transcribe_audio(fake_audio)

    labels = [s.speaker_label for s in segments]
    assert labels[2] == labels[0]                 # same boss across windows
    assert labels[3] not in (labels[0], labels[1])  # a genuinely new voice gets a new label
    assert len(_clip_markers(calls[1])) == 2


@pytest.mark.asyncio
async def test_transcribe_audio_never_reuses_labels_already_taken_in_the_project(monkeypatch, fake_audio):
    """Episode 2's first voice must not silently inherit episode 1's SPEAKER_00 (name + voice)."""
    async def fake_spans(path):
        return [(0.0, 2.0)]

    async def fake_call(parts):
        return [{"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "你好"}]}]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, _ = await asr.transcribe_audio(fake_audio, taken_labels={"SPEAKER_00", "SPEAKER_01"})
    assert segments[0].speaker_label == "SPEAKER_02"


@pytest.mark.asyncio
async def test_transcribe_audio_matches_a_project_reference_voice(monkeypatch, fake_audio, tmp_path):
    ref = SpeakerReference(label="SPEAKER_04", clip_path=_write(tmp_path, "ref.wav"), text="我叫林冬", duration=2.0)

    async def fake_spans(path):
        return [(0.0, 2.0)]

    async def fake_call(parts):
        rid = next(iter(_reference_ids_by_text(parts)))
        return [{"clip": 1, "no_speech": False, "turns": [{"speaker": rid, "text": "又见面了"}]}]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, _ = await asr.transcribe_audio(fake_audio, references=[ref], taken_labels={"SPEAKER_04"})
    assert segments[0].speaker_label == "SPEAKER_04"


@pytest.mark.asyncio
async def test_transcribe_audio_failed_window_keeps_timing_with_empty_text(monkeypatch, fake_audio):
    monkeypatch.setattr(asr, "ASR_MIN_SPLIT_CLIPS", 99)   # no split-retry: fail the window outright

    async def fake_spans(path):
        return [(0.0, 2.0), (3.0, 5.0)]

    async def failing_call(parts):
        raise RuntimeError("Gemini ASR error 503")

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", failing_call)
    segments, used_mock = await asr.transcribe_audio(fake_audio)
    assert used_mock is True
    assert [(s.start_time, s.end_time, s.source_text) for s in segments] == [(0.0, 2.0, ""), (3.0, 5.0, "")]


@pytest.mark.asyncio
async def test_transcribe_audio_retries_a_failed_window_as_two_halves(monkeypatch, fake_audio):
    monkeypatch.setattr(asr, "ASR_MIN_SPLIT_CLIPS", 2)
    sizes = []

    async def fake_spans(path):
        return [(float(i), i + 0.8) for i in range(4)]

    async def flaky_call(parts):
        n = len(_clip_markers(parts))
        sizes.append(n)
        if n == 4:
            raise RuntimeError("payload too large")
        return [{"clip": k, "no_speech": False, "turns": [{"speaker": "A", "text": f"第{k}句"}]} for k in range(1, n + 1)]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", flaky_call)
    segments, used_mock = await asr.transcribe_audio(fake_audio)
    assert sizes == [4, 2, 2]
    assert used_mock is False
    assert all(s.source_text for s in segments) and len(segments) == 4


@pytest.mark.asyncio
async def test_transcribe_audio_stops_calling_a_service_that_keeps_failing(monkeypatch, fake_audio):
    """Circuit breaker: an outage must not cost minutes of retries per window."""
    monkeypatch.setattr(asr, "ASR_WINDOW_MAX_CLIPS", 2)
    monkeypatch.setattr(asr, "ASR_MIN_SPLIT_CLIPS", 99)
    calls = []

    async def fake_spans(path):
        return [(float(i), i + 0.8) for i in range(8)]

    async def failing_call(parts):
        calls.append(1)
        raise RuntimeError("Gemini ASR request failed: ReadTimeout")

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", failing_call)
    segments, used_mock = await asr.transcribe_audio(fake_audio)
    assert len(calls) == asr.ASR_CIRCUIT_BREAKER_FAILURES
    assert used_mock is True and len(segments) == 8 and not any(s.source_text for s in segments)


@pytest.mark.asyncio
async def test_transcribe_audio_without_an_api_key_skips_gemini(monkeypatch, fake_audio):
    monkeypatch.setattr(asr.settings, "GEMINI_API_KEY", "")
    called = []

    async def fake_spans(path):
        return [(0.0, 1.0)]

    async def fake_call(parts):
        called.append(1)
        return []

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, used_mock = await asr.transcribe_audio(fake_audio)
    assert called == [] and used_mock is True
    assert [(s.start_time, s.source_text) for s in segments] == [(0.0, "")]


@pytest.mark.asyncio
async def test_rejected_request_is_not_split_and_retried(monkeypatch, fake_audio):
    """A 4xx (bad key, bad model name) fails the same way at any size — don't multiply it."""
    monkeypatch.setattr(asr, "ASR_MIN_SPLIT_CLIPS", 2)
    calls = []

    async def fake_spans(path):
        return [(float(i), i + 0.8) for i in range(4)]

    async def rejected(parts):
        calls.append(1)
        raise asr.GeminiRequestRejected("Gemini ASR error 404: model not found")

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", rejected)
    _, used_mock = await asr.transcribe_audio(fake_audio)
    assert used_mock is True and len(calls) == 1


@pytest.mark.asyncio
async def test_label_speech_returns_one_label_per_span_from_the_dominant_turn(monkeypatch, fake_audio):
    async def fake_call(parts):
        return [
            {"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "嗯"},
                                                      {"speaker": "B", "text": "这句话比较长"}]},
            {"clip": 2, "no_speech": True, "turns": []},
            {"clip": 3, "no_speech": False, "turns": [{"speaker": "A", "text": "对"}]},
        ]

    monkeypatch.setattr(asr, "_call_window", fake_call)
    labels, texts, used_mock = await asr.label_speech(fake_audio, [(0, 3), (4, 5), (6, 7)])
    assert len(labels) == 3
    assert labels[0] == "SPEAKER_01"          # B said more of clip 1
    assert labels[1] == labels[0]             # no-speech span inherits the previous voice
    assert labels[2] == "SPEAKER_00"
    assert texts == ["嗯这句话比较长", "", "对"]
    assert used_mock is False


# ── HTTP retry policy ─────────────────────────────────────────────────────────

def _gemini_ok(rows):
    return httpx.Response(200, json={"candidates": [{
        "finishReason": "STOP",
        "content": {"parts": [{"text": json.dumps(rows, ensure_ascii=False)}]},
    }]})


@pytest.fixture
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(asr, "_sleep", instant)


def _use_transport(monkeypatch, handler):
    monkeypatch.setattr(asr, "_new_http_client",
                        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout))


@pytest.mark.asyncio
async def test_call_window_retries_a_busy_service_then_succeeds(monkeypatch, no_sleep):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return _gemini_ok([{"clip": 1, "no_speech": True, "turns": []}])

    _use_transport(monkeypatch, handler)
    rows = await asr._call_window([{"text": "x"}])
    assert len(attempts) == 3
    assert rows == [{"clip": 1, "no_speech": True, "turns": []}]


@pytest.mark.asyncio
async def test_call_window_does_not_retry_a_rejected_request(monkeypatch, no_sleep):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(400, json={"error": {"message": "API key not valid"}})

    _use_transport(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="400"):
        await asr._call_window([{"text": "x"}])
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_call_window_treats_truncated_output_as_a_failure(monkeypatch, no_sleep):
    def handler(request):
        return httpx.Response(200, json={"candidates": [{
            "finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '[{"clip": 1'}]}}]})

    _use_transport(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="MAX_TOKENS"):
        await asr._call_window([{"text": "x"}])


@pytest.mark.asyncio
async def test_call_window_sends_json_schema_and_low_thinking(monkeypatch, no_sleep):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _gemini_ok([])

    _use_transport(monkeypatch, handler)
    await asr._call_window([{"text": "x"}])
    cfg = seen["body"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    assert "responseSchema" in cfg
    assert cfg["thinkingConfig"]["thinkingLevel"] == "low"


@pytest.mark.asyncio
async def test_a_retired_transcription_model_is_replaced_without_using_up_retries(monkeypatch, no_sleep):
    from app.services import gemini_models
    gemini_models.reset()
    monkeypatch.setattr(asr.settings, "GEMINI_ASR_MODEL", "gemini-3.6-flash")
    models = []

    def handler(request):
        model = request.url.path.split("/models/")[1].split(":")[0]
        models.append(model)
        if model == "gemini-3.6-flash":
            return httpx.Response(404, json={"error": {"code": 404, "message":
                "This model models/gemini-3.6-flash is no longer available to new users."}})
        return _gemini_ok([{"clip": 1, "no_speech": True, "turns": []}])

    _use_transport(monkeypatch, handler)
    rows = await asr._call_window([{"text": "x"}])
    assert rows == [{"clip": 1, "no_speech": True, "turns": []}]
    assert models == ["gemini-3.6-flash", "gemini-flash-latest"]
    gemini_models.reset()


# ── Who each voice sounds like ────────────────────────────────────────────────
# The pitch of the lines called four of five men in a real drama women or
# children (excited young men speak at 230–250 Hz). Gemini, which hears the
# whole voice and the conversation, now says it per turn.

def test_the_prompt_asks_who_each_voice_sounds_like():
    prompt = asr._window_prompt(3, False, "zh")
    assert "man, woman, boy or girl" in prompt


def test_parse_window_voices_keeps_known_voices_in_turn_order():
    rows = [
        {"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "走", "voice": "man"},
                                                  {"speaker": "B", "text": "", "voice": "woman"},     # dropped turn
                                                  {"speaker": "B", "text": "好", "voice": "robot"}]},  # unknown voice
        {"clip": 2, "no_speech": True, "turns": []},
    ]
    assert asr._parse_window_voices(rows, 2) == {1: ["man", None]}


@pytest.mark.asyncio
async def test_transcribed_lines_carry_who_the_voice_sounds_like(monkeypatch, fake_audio):
    async def fake_spans(path):
        return [(0.0, 2.0), (3.0, 5.0), (6.0, 8.0)]

    async def fake_call(parts):
        return [
            {"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "你好", "voice": "man"}]},
            {"clip": 2, "no_speech": False, "turns": [{"speaker": "B", "text": "嗯", "voice": "woman"}]},
            {"clip": 3, "no_speech": False, "turns": [{"speaker": "A", "text": "走吧", "voice": "woman"},
                                                      {"speaker": "A", "text": "快", "voice": "man"}]},
        ]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, _ = await asr.transcribe_audio(fake_audio)
    by_label = {s.speaker_label: s.gender for s in segments}
    assert by_label == {"SPEAKER_00": "man", "SPEAKER_01": "woman"}     # A: man by 2 turns to 1


def test_a_reference_voice_says_who_it_is_and_what_else_they_said(tmp_path):
    """
    Measured on a real project (leave-one-episode-out, 130 lines): Gemini named
    the right character for 36–42% of lines from a voice clip alone and 58%
    when each reference also said "a man, 20s" and a few of that character's
    other lines — young men's voices sound alike; what they say tells them apart.
    """
    ref = SpeakerReference(label="SPEAKER_04", clip_path=_write(tmp_path, "r.wav"), text="他敢走一个试试！",
                           duration=2.0, about="a man, 20s", said=("我爸是这的校长。", "你现在敢走？"))
    parts = _build_window_parts([(_write(tmp_path, "a.wav"), 1.0)], refs=[("R1", ref)])
    texts = [p.get("text", "") for p in parts]
    i = next(i for i, t in enumerate(texts) if t.startswith("Reference voice R1"))
    assert "a man, 20s" in texts[i] and "他敢走一个试试！" in texts[i]
    assert "inlineData" in parts[i + 1]
    assert texts[i + 2].startswith("R1 also said") and "「我爸是这的校长。」" in texts[i + 2]


@pytest.mark.asyncio
async def test_a_clip_whose_text_cannot_fit_its_length_is_asked_for_again(monkeypatch, fake_audio):
    """A real 90 s episode once came back with 1,543 characters (about 200 normally): Gemini looped on a clip."""
    async def fake_spans(path):
        return [(0.0, 2.0), (3.0, 5.0)]

    calls = []

    async def fake_call(parts):
        calls.append(_clip_markers(parts))
        if len(calls) == 1:
            return [{"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "你好"}]},
                    {"clip": 2, "no_speech": False, "turns": [{"speaker": "A", "text": "哈" * 300}]}]
        return [{"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "再见"}]}]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    segments, used_mock = await asr.transcribe_audio(fake_audio)
    assert [s.source_text for s in segments] == ["你好", "再见"]
    assert len(calls) == 2 and len(calls[1]) == 1          # only the runaway clip was asked again


# ── Gemini hears the original sound ───────────────────────────────────────────
# The separation service's vocals lose everything below ~180 Hz (0.1–1.4% of the
# energy there vs 17–33% in the original): men sound thin, and Gemini called 3
# of 6 speakers' genders right from them, 7 of 7 from the original sound, with
# transcription as good or better. Pauses are still found on the vocals.

@pytest.mark.asyncio
async def test_clips_can_be_cut_from_the_original_sound(monkeypatch, fake_audio):
    detected_on, cut_from = [], []

    async def fake_spans(path):
        detected_on.append(path)
        return [(0.0, 2.0)]

    async def fake_slice(audio_path, start, end, out_path):
        cut_from.append(audio_path)
        with open(out_path, "wb") as f:
            f.write(b"RIFF")

    async def fake_call(parts):
        return [{"clip": 1, "no_speech": False, "turns": [{"speaker": "A", "text": "你好"}]}]

    monkeypatch.setattr(asr, "_detect_speech_segments", fake_spans)
    monkeypatch.setattr(asr, "_slice_chunk", fake_slice)
    monkeypatch.setattr(asr, "_call_window", fake_call)
    await asr.transcribe_audio(fake_audio, clip_audio="/media/source.mp4")
    assert detected_on == [fake_audio] and cut_from == ["/media/source.mp4"]


def test_the_prompt_leaves_out_song_lyrics():
    assert "lyrics" in asr._window_prompt(2, False, "zh")
