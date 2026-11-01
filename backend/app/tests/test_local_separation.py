"""
Separation on this Mac (Spleeter, 2 stems, run by sherpa-onnx): the model is downloaded once into the data folder,
long sound is separated in pieces that cross-fade, and the HF Space is only the fallback.
"""
import asyncio
import hashlib
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.services import local_separation, source_separator
from app.services.characters import models


def _split(samples: np.ndarray, rate: int):
    """A fake model: a quarter of the sound is "vocals", the rest "music"."""
    return samples * 0.25, samples * 0.75


def test_long_sound_is_separated_in_pieces_that_join_seamlessly():
    rate = 100
    sound = np.random.default_rng(0).standard_normal((2, rate * 610)).astype(np.float32)
    calls = []

    def run(piece, r):
        calls.append(piece.shape[1])
        return _split(piece, r)

    vocals, music = local_separation.separate_samples(sound, rate, run)
    assert vocals.shape == music.shape == sound.shape
    assert np.allclose(vocals, sound * 0.25, atol=1e-5) and np.allclose(music, sound * 0.75, atol=1e-5)
    assert len(calls) > 1 and max(calls) <= rate * local_separation.PIECE_SECS


def test_a_short_sound_is_one_piece():
    rate = 100
    sound = np.ones((2, int(rate * local_separation.PIECE_SECS) - 1), np.float32)
    calls = []
    local_separation.separate_samples(sound, rate, lambda p, r: calls.append(1) or _split(p, r))
    assert calls == [1]


# ── the download ──────────────────────────────────────────────

def _serve(monkeypatch, bodies: dict[str, bytes], asked: list | None = None):
    real = httpx.AsyncClient

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if asked is not None:
            asked.append(name)
        return httpx.Response(200, content=bodies[name])

    monkeypatch.setattr(local_separation.httpx, "AsyncClient",
                        lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))


def test_the_model_is_downloaded_and_checked_once(monkeypatch, tmp_path):
    bodies = {name: name.encode() * 10 for name in local_separation.FILES}
    monkeypatch.setattr(local_separation.settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(local_separation, "FILES", {n: hashlib.sha256(b).hexdigest() for n, b in bodies.items()})
    asked = []
    _serve(monkeypatch, bodies, asked)

    assert asyncio.run(local_separation.ensure_model()) is True
    assert local_separation.model_ready()
    assert sorted(asked) == sorted(bodies)
    assert asyncio.run(local_separation.ensure_model()) is True   # already there: nothing downloaded again
    assert len(asked) == len(bodies)
    assert not list(local_separation.model_dir().parent.glob("*.part"))


def test_a_download_with_the_wrong_checksum_is_thrown_away_and_not_tried_again(monkeypatch, tmp_path):
    monkeypatch.setattr(local_separation.settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(local_separation, "FILES", {name: "0" * 64 for name in local_separation.FILES})
    asked = []
    _serve(monkeypatch, {name: b"something else" for name in local_separation.FILES}, asked)

    assert asyncio.run(local_separation.ensure_model()) is False
    assert not local_separation.model_ready()
    assert not any(p.suffix == ".onnx" for p in tmp_path.rglob("*"))
    tried = len(asked)
    assert asyncio.run(local_separation.ensure_model()) is False   # this app version doesn't try again
    assert len(asked) == tried


def test_without_a_data_folder_nothing_is_downloaded(monkeypatch):
    monkeypatch.setattr(local_separation.settings, "DUBIFY_DATA_DIR", "")
    assert asyncio.run(local_separation.ensure_model()) is False
    assert not local_separation.model_ready()


def test_a_mac_that_cannot_run_the_model_downloads_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(local_separation.settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "runtime_supported", lambda: False)
    asked = []
    _serve(monkeypatch, {}, asked)
    assert asyncio.run(local_separation.ensure_model()) is False
    assert asked == []


# ── which way separates ──────────────────────────────────────

def _audio(tmp_path: Path) -> Path:
    audio = tmp_path / "source_audio.wav"
    audio.write_bytes(b"RIFFfake")
    return audio


def _local_works(monkeypatch, calls):
    async def separate(audio_path, vocals_target, bgm_target, timeout):
        calls.append(audio_path)
        Path(vocals_target).write_bytes(b"RIFFvocals")
        Path(bgm_target).write_bytes(b"RIFFmusic")
    monkeypatch.setattr(local_separation, "model_ready", lambda: True)
    monkeypatch.setattr(local_separation, "separate", separate)


def _no_copies(monkeypatch):
    async def nothing(*args):
        return None
    monkeypatch.setattr(source_separator, "_create_preview", nothing)
    monkeypatch.setattr(source_separator, "_create_playback_m4a", nothing)


@pytest.mark.asyncio
async def test_separation_runs_on_this_mac_when_the_model_is_there(monkeypatch, tmp_path):
    calls = []
    _local_works(monkeypatch, calls)
    _no_copies(monkeypatch)
    monkeypatch.setattr(source_separator, "_hf_separate_call", lambda path: pytest.fail("the Space was asked"))

    vocals, music, degraded = await source_separator.separate_vocals_bgm(str(_audio(tmp_path)), str(tmp_path))
    assert not degraded and len(calls) == 1
    assert Path(vocals).read_bytes() == b"RIFFvocals" and Path(music).read_bytes() == b"RIFFmusic"
    assert not (tmp_path / source_separator.UNAVAILABLE_MARKER).exists()


@pytest.mark.asyncio
async def test_the_space_separates_when_this_mac_fails(monkeypatch, tmp_path):
    async def broken(audio_path, vocals_target, bgm_target, timeout):
        Path(vocals_target).write_bytes(b"half")   # a failure part-way leaves nothing behind
        raise RuntimeError("out of memory")
    monkeypatch.setattr(local_separation, "model_ready", lambda: True)
    monkeypatch.setattr(local_separation, "separate", broken)
    _no_copies(monkeypatch)
    stems = tmp_path / "space"
    stems.mkdir()
    (stems / "v.wav").write_bytes(b"x")
    (stems / "m.wav").write_bytes(b"x")
    asked = []
    monkeypatch.setattr(source_separator, "_hf_separate_call",
                        lambda path: asked.append(path) or (str(stems / "v.wav"), str(stems / "m.wav")))

    async def to_wav(src, dest):
        Path(dest).write_bytes(b"RIFFspace")
        return True
    monkeypatch.setattr(source_separator, "_to_wav", to_wav)

    vocals, music, degraded = await source_separator.separate_vocals_bgm(str(_audio(tmp_path)), str(tmp_path))
    assert not degraded and len(asked) == 1
    assert Path(vocals).read_bytes() == b"RIFFspace"


@pytest.mark.asyncio
async def test_try_again_separates_on_this_mac_too(monkeypatch, tmp_path):
    (tmp_path / "vocals.wav").write_bytes(b"RIFFold")
    (tmp_path / "no_vocals.wav").write_bytes(b"RIFFold")
    (tmp_path / source_separator.UNAVAILABLE_MARKER).write_text("The separation service failed\n")
    calls = []
    _local_works(monkeypatch, calls)
    _no_copies(monkeypatch)
    monkeypatch.setattr(source_separator, "_hf_separate_call", lambda path: pytest.fail("the Space was asked"))

    vocals, music, degraded = await source_separator.separate_vocals_bgm(
        str(_audio(tmp_path)), str(tmp_path), force=True)
    assert not degraded and len(calls) == 1
    assert Path(vocals).read_bytes() == b"RIFFvocals"
    assert not (tmp_path / source_separator.UNAVAILABLE_MARKER).exists()


@pytest.mark.asyncio
async def test_a_failing_child_raises_and_leaves_no_process(monkeypatch, tmp_path):
    """The real child process, given a file it can't read: separate raises (the caller then asks the Space)."""
    monkeypatch.setenv("DUBIFY_DATA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="exit"):
        await local_separation.separate(str(tmp_path / "missing.wav"), str(tmp_path / "v.wav"),
                                        str(tmp_path / "m.wav"), timeout=120)
    assert not (tmp_path / "v.wav").exists()
