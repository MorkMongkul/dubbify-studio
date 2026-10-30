"""How hard character recognition works the Mac. Measured 2026-10-29 on the owner's M1 MacBook Air (12 lines of a
real episode): onnxruntime's defaults — a thread per core that spins between frames — kept 4 cores busy (103 CPU-s in
26 s) and, over the start-up's hour of older episodes, heated the fanless Mac to 97 °C. Two threads that never spin:
68 CPU-s in 37 s, the same prints. Since 0.5.3 only the two voice models run, 2 threads each. The start-up's work,
which nobody waits for, runs at background priority (the efficiency cores)."""
import asyncio
import ctypes
import sys

import pytest

from app.services.characters import models, store, voiceprint
from app.services.characters.priority import BACKGROUND, DEFAULT


def test_both_voice_models_use_two_threads(monkeypatch):
    sherpa_onnx = pytest.importorskip("sherpa_onnx")
    seen = []
    monkeypatch.setattr(sherpa_onnx, "SpeakerEmbeddingExtractorConfig",
                        lambda model, num_threads: seen.append((model, num_threads)))
    monkeypatch.setattr(sherpa_onnx, "SpeakerEmbeddingExtractor", lambda config: object())
    monkeypatch.setattr(models, "path", lambda name: f"/models/{name}.onnx")
    voiceprint._extractor.cache_clear(); voiceprint._campplus.cache_clear()
    try:
        voiceprint._extractor(); voiceprint._campplus()
    finally:
        voiceprint._extractor.cache_clear(); voiceprint._campplus.cache_clear()
    assert seen == [("/models/eres2netv2.onnx", 2), ("/models/campplus.onnx", 2)]


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


