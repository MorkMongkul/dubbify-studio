"""How hard character recognition works the Mac. Measured 2026-10-29 on the owner's M1 MacBook Air (12 lines of a
real episode): onnxruntime's defaults — a thread per core that spins between frames — kept 4 cores busy (103 CPU-s in
26 s) and, over the start-up's hour of older episodes, heated the fanless Mac to 97 °C. Two threads that never spin:
68 CPU-s in 37 s, the same prints. The start-up's work, which nobody waits for, runs at background priority (the
efficiency cores)."""
import asyncio
import ctypes
import sys

import pytest

from app.services.characters import faces, lips, models, store, voiceprint

from app.services.characters.priority import BACKGROUND, DEFAULT


def test_model_sessions_use_two_threads_that_never_spin():
    pytest.importorskip("onnxruntime")
    options = models.session_options()
    assert options.intra_op_num_threads == 2
    assert options.inter_op_num_threads == 1
    assert options.get_session_config_entry("session.intra_op.allow_spinning") == "0"
    assert options.get_session_config_entry("session.inter_op.allow_spinning") == "0"


def test_the_face_and_lip_models_are_loaded_with_those_options(monkeypatch):
    onnxruntime = pytest.importorskip("onnxruntime")
    made = []
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda path, options, providers: made.append(options))
    monkeypatch.setattr(models, "path", lambda name: f"/models/{name}.onnx")
    faces._session.cache_clear(); lips._session.cache_clear()
    try:
        faces._session("yunet"); faces._session("sface"); lips._session()
    finally:
        faces._session.cache_clear(); lips._session.cache_clear()
    assert [o.intra_op_num_threads for o in made] == [2, 2, 2]
    assert all(o.get_session_config_entry("session.intra_op.allow_spinning") == "0" for o in made)


def test_the_voice_model_uses_two_threads(monkeypatch):
    sherpa_onnx = pytest.importorskip("sherpa_onnx")
    seen = {}
    monkeypatch.setattr(sherpa_onnx, "SpeakerEmbeddingExtractorConfig",
                        lambda model, num_threads: seen.update(threads=num_threads))
    monkeypatch.setattr(sherpa_onnx, "SpeakerEmbeddingExtractor", lambda config: object())
    monkeypatch.setattr(models, "path", lambda name: "/models/voice.onnx")
    voiceprint._extractor.cache_clear()
    try:
        voiceprint._extractor()
    finally:
        voiceprint._extractor.cache_clear()
    assert seen["threads"] == 2


def test_the_picture_is_decoded_with_two_threads(monkeypatch):
    seen = []

    class Started(Exception):
        pass

    def popen(args, **kwargs):
        seen.append(args)
        raise Started

    monkeypatch.setattr(faces.subprocess, "Popen", popen)
    with pytest.raises(Started):
        list(faces.frames("/episode.mp4", 0.0, 1.0, size=(64, 36)))
    args = seen[0]
    assert args.index("-threads") < args.index("-i") and args[args.index("-threads") + 1] == "2"
    assert args[args.index("-filter_threads") + 1] == "1"


def _qos() -> int:
    lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
    lib.qos_class_self.restype = ctypes.c_uint
    return lib.qos_class_self()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS thread priorities")
def test_the_start_up_work_runs_at_background_priority_and_an_analysis_does_not():
    seen = []

    async def main():
        await asyncio.to_thread(store._one_at_a_time, lambda: seen.append(_qos()))       # an analysis
        with store.at_background_priority():                                             # the start-up's work
            await asyncio.to_thread(store._one_at_a_time, lambda: seen.append(_qos()))
        await asyncio.to_thread(store._one_at_a_time, lambda: seen.append(_qos()))       # the thread went back

    asyncio.run(main())
    assert seen == [DEFAULT, BACKGROUND, DEFAULT]


def test_background_work_never_shares_loaded_models_with_an_analysis(monkeypatch):
    """A model's threads keep the priority they were made at, so background work loads its own and drops them."""
    released = []
    monkeypatch.setattr(store, "release_models", lambda: released.append(len(seen)))
    seen = []

    async def main():
        with store.at_background_priority():
            await asyncio.to_thread(store._one_at_a_time, lambda: seen.append("background"))

    asyncio.run(main())
    assert released == [0, 1]   # before it ran and after


def test_the_start_up_upgrade_runs_in_the_background(monkeypatch):
    from app.services.characters import upgrade
    seen = []

    async def work(sessionmaker):
        seen.append(store._BACKGROUND.get())

    monkeypatch.setattr(upgrade, "_finish_upgrade", work)
    asyncio.run(upgrade.finish_upgrade(None))
    assert seen == [True]
    assert store._BACKGROUND.get() is False


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS thread priorities")
def test_an_intel_mac_times_its_face_step_at_normal_priority_even_from_background_work(monkeypatch):
    """Timed on the efficiency cores, an Intel Mac would turn faces off for good (for this app version)."""
    from app.services.characters import speed
    seen = []
    monkeypatch.setattr(speed.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(speed, "_memo", None)
    monkeypatch.setattr(speed, "_read_cache", lambda version: None)
    monkeypatch.setattr(speed, "_write_cache", lambda answer: None)
    monkeypatch.setattr(speed, "measure", lambda: seen.append(_qos()) or speed.Parts(1, 1, 1, 1, 1))

    async def main():
        with store.at_background_priority():
            await asyncio.to_thread(store._one_at_a_time, speed.face_step_enabled)

    asyncio.run(main())
    assert seen == [DEFAULT]


def test_recognition_never_reads_the_picture(monkeypatch):
    """0.5.2: voice only — the face step (every frame through three models) took 2–3 min per episode."""
    from app.services.characters import prints, speed
    seen = {}

    def prints_for_spans(video, vocals, spans, with_faces=True):
        seen["with_faces"] = with_faces
        return []

    def never():
        raise AssertionError("the face step's speed was timed")

    monkeypatch.setattr(models, "runtime_supported", lambda: True)
    monkeypatch.setattr(prints, "prints_for_spans", prints_for_spans)
    monkeypatch.setattr(speed, "face_step_enabled", never)
    store._prints_of("/episode.mp4", "/vocals.wav", [(0.0, 1.0)])
    assert seen == {"with_faces": False}
