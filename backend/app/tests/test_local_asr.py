"""
Transcription on this Mac (0.5.6): Qwen3-ASR given the Story notes' names, a voice-kind model (man/woman) and the
voice prints, in a child process; the voices grouped into SPEAKER_XX labels the project hasn't used. Gemini
transcribes only while the models are downloading, when they can't run, or when this fails.
"""
import asyncio
import hashlib

import httpx
import numpy as np
import pytest

from app.services import local_asr, model_files, pipeline
from app.services.characters import models


# ── what Qwen3-ASR wrote, cleaned ────────────────────────────────

NAMES = ["阿拿", "薛次安", "秦轩", "山魈"]


def test_the_names_list_read_back_is_removed():
    """Given names to listen for, Qwen3-ASR sometimes writes them all out on a quiet clip (10 times in 10 episodes)."""
    echo = " ".join(NAMES)
    assert local_asr.clean_text(f"准备吧。{echo}。我们走", NAMES) == "准备吧。我们走"
    assert local_asr.clean_text(echo, NAMES) == ""
    assert local_asr.clean_text("。", NAMES) == ""
    assert local_asr.clean_text("薛次安，救我！", NAMES) == "薛次安，救我！"   # a name that was said stays


def test_text_comes_out_simplified():
    assert local_asr.clean_text("這是什麼", []) == "这是什么"


def test_names_come_from_the_story_notes():
    notes = {"characters": [{"zh": "薛次安"}, {"zh": "SPEAKER_03"}, {"zh": "雯雯"}, {"zh": ""}],
             "words": [{"zh": "山魈"}, {"zh": "薛次安"}]}
    assert local_asr.names_from_notes(notes) == ["薛次安", "雯雯", "山魈"]
    assert local_asr.names_from_notes(None) == []


# ── voices grouped into speakers ─────────────────────────────────

def _unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def test_voices_are_grouped_by_their_prints():
    a, b = _unit([1, 0.1, 0]), _unit([0, 1, 0.1])
    prints = [a, b, _unit([0.95, 0.12, 0.02]), None, _unit([0.05, 0.98, 0.1])]
    groups = local_asr.group_voices(prints)
    assert groups[0] == groups[2] and groups[1] == groups[4] and groups[0] != groups[1]
    assert groups[3] == groups[2]   # too short for a print: the voice of the line nearest before it


def test_one_print_or_none_is_one_speaker():
    assert local_asr.group_voices([None, None]) == [0, 0]
    assert local_asr.group_voices([_unit([1, 0])]) == [0]


# ── one episode ──────────────────────────────────────────────────

def _heard(monkeypatch, clips, spans=((0.0, 2.0), (3.0, 5.0), (6.0, 8.0), (9.0, 9.5))):
    async def speech(path):
        return list(spans)
    monkeypatch.setattr(local_asr.asr, "_detect_speech_segments", speech)

    async def child(request, timeout):
        assert request["names"] == NAMES and len(request["spans"]) == len(spans)
        return {"clips": clips}
    monkeypatch.setattr(local_asr, "_run_child", child)


@pytest.mark.asyncio
async def test_an_episode_becomes_lines_with_new_labels_and_voices(monkeypatch):
    man, woman = _unit([1, 0, 0]).tolist(), _unit([0, 1, 0]).tolist()
    _heard(monkeypatch, [
        {"text": "你好", "voice": "man", "print": man},
        {"text": "你是谁？", "voice": "woman", "print": woman},
        {"text": "薛次安", "voice": "man", "print": man},
        {"text": "", "voice": "man", "print": None},          # nothing said: no line
    ])
    lines = await local_asr.transcribe("v.wav", "o.mp4", NAMES, taken_labels=["SPEAKER_00", "SPEAKER_02"])
    assert [(s.start_time, s.source_text) for s in lines] == [(0.0, "你好"), (3.0, "你是谁？"), (6.0, "薛次安")]
    assert lines[0].speaker_label == lines[2].speaker_label == "SPEAKER_01"   # labels the project hasn't used
    assert lines[1].speaker_label == "SPEAKER_03"
    assert [s.gender for s in lines] == ["man", "woman", "man"]


@pytest.mark.asyncio
async def test_a_speakers_voice_is_what_most_of_their_lines_sound_like(monkeypatch):
    p = _unit([1, 0, 0]).tolist()
    _heard(monkeypatch, [{"text": "一", "voice": "man", "print": p}, {"text": "二", "voice": "woman", "print": p},
                         {"text": "三", "voice": "man", "print": p}, {"text": "四", "voice": "man", "print": p}])
    lines = await local_asr.transcribe("v.wav", "o.mp4", NAMES, taken_labels=[])
    assert {s.gender for s in lines} == {"man"}


@pytest.mark.asyncio
async def test_a_clip_that_loops_is_left_out(monkeypatch):
    p = _unit([1, 0, 0]).tolist()
    _heard(monkeypatch, [{"text": "啊" * 200, "voice": "man", "print": p}], spans=((0.0, 2.0),))
    assert await local_asr.transcribe("v.wav", "o.mp4", NAMES, taken_labels=[]) == []


# ── the model ────────────────────────────────────────────────────

def test_nothing_is_ready_without_a_data_folder(monkeypatch):
    monkeypatch.setattr(local_asr.settings, "DUBIFY_DATA_DIR", "")
    assert not local_asr.model_ready()


def test_both_models_are_downloaded_and_checked(monkeypatch, tmp_path):
    monkeypatch.setattr(local_asr.settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "runtime_supported", lambda: True)
    bodies = {}
    for spec in local_asr.MODELS:
        spec_files = {}
        for name in spec.files:
            bodies[name] = f"{spec.folder}/{name}".encode()
            spec_files[name] = hashlib.sha256(bodies[name]).hexdigest()
        monkeypatch.setattr(spec, "files", spec_files)
    real = httpx.AsyncClient

    def handler(request):
        name = request.url.path.split("/resolve/", 1)[1].split("/", 1)[1]
        return httpx.Response(200, content=bodies[name])
    monkeypatch.setattr(model_files.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(local_asr.ensure_model()) is True
    assert local_asr.model_ready()
    assert (tmp_path / "models" / local_asr.MODELS[0].folder / "tokenizer" / "vocab.json").is_file()


@pytest.mark.asyncio
async def test_a_failing_child_raises(monkeypatch, tmp_path):
    """The real child, given audio it can't read: transcribe raises (the analysis then asks Gemini)."""
    monkeypatch.setenv("DUBIFY_DATA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="exit"):
        await local_asr._run_child({"vocals": str(tmp_path / "missing.wav"), "audio": str(tmp_path / "missing.wav"),
                                    "spans": [[0.0, 1.0]], "names": []}, timeout=120)


# ── which way an analysis transcribes ────────────────────────────

@pytest.mark.parametrize("setting,ready,expected", [
    ("mac", True, "mac"), ("mac", False, "gemini"), ("gemini", True, "gemini")])
def test_which_way_transcribes(monkeypatch, setting, ready, expected):
    monkeypatch.setattr(local_asr.settings, "TRANSCRIPTION", setting)
    monkeypatch.setattr(local_asr, "model_ready", lambda: ready)
    assert local_asr.use_mac("zh") == (expected == "mac")
    assert not local_asr.use_mac("en")   # Qwen3-ASR is given Chinese names; other languages stay with Gemini


@pytest.mark.asyncio
async def test_gemini_transcribes_when_the_mac_fails(monkeypatch):
    monkeypatch.setattr(local_asr, "use_mac", lambda language: True)

    async def broken(*args, **kwargs):
        raise RuntimeError("out of memory")
    monkeypatch.setattr(local_asr, "transcribe", broken)
    asked = []

    async def gemini(*args, **kwargs):
        asked.append(kwargs)
        return ["gemini's lines"], False
    monkeypatch.setattr(pipeline, "transcribe_audio", gemini)
    lines, mock = await pipeline.transcribe_episode("v.wav", "o.mp4", [], [], "zh", NAMES)
    assert lines == ["gemini's lines"] and len(asked) == 1


@pytest.mark.asyncio
async def test_the_mac_transcribes_without_asking_gemini(monkeypatch):
    monkeypatch.setattr(local_asr, "use_mac", lambda language: True)

    async def here(vocals, audio, names, taken_labels):
        return ["mac lines"]
    monkeypatch.setattr(local_asr, "transcribe", here)

    async def gemini(*args, **kwargs):
        pytest.fail("Gemini was asked")
    monkeypatch.setattr(pipeline, "transcribe_audio", gemini)
    assert await pipeline.transcribe_episode("v.wav", "o.mp4", [], [], "zh", NAMES) == (["mac lines"], False)
