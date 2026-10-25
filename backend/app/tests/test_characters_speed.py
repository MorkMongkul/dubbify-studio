"""
Characters: the face step is switched off on a Mac too slow for it (characters.speed).

The timed workload is replaced at the service boundary (speed.measure), so no test runs a model.
"""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.characters import prints as prints_module
from app.services.characters import speed, store


def parts(total: float) -> speed.Parts:
    """Parts whose face step takes `total` s (the voice's share is not part of it)."""
    return speed.Parts(decode=total / 4, yunet=total / 4, light_asd=total / 4, sface=total / 4, voice=999.0)


def counting(monkeypatch, total: float) -> list[int]:
    calls: list[int] = []

    def measure():
        calls.append(1)
        return parts(total)

    monkeypatch.setattr(speed, "measure", measure)
    return calls


def test_a_slow_estimate_turns_faces_off(monkeypatch):
    counting(monkeypatch, 200.0)
    assert speed.face_step_enabled() is False


def test_a_fast_estimate_and_the_budget_edge_keep_faces_on(monkeypatch):
    counting(monkeypatch, speed.FACE_STEP_BUDGET_SECS)
    assert speed.face_step_enabled() is True   # the voice's 999 s don't count against it


def test_the_estimate_is_cached_per_app_version(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "DUBIFY_APP_VERSION", "1.0.0")
    calls = counting(monkeypatch, 200.0)

    assert speed.face_step_enabled() is False
    saved = json.loads((tmp_path / "character-speed.json").read_text())
    assert saved["app_version"] == "1.0.0" and saved["faces"] is False and saved["estimate_secs"] == 200.0
    assert "measured_at" in saved

    assert speed.face_step_enabled() is False
    monkeypatch.setattr(speed, "_memo", None)      # a new process reads the file
    assert speed.face_step_enabled() is False
    assert len(calls) == 1

    monkeypatch.setattr(settings, "DUBIFY_APP_VERSION", "1.0.1")
    assert speed.face_step_enabled() is False
    assert len(calls) == 2
    assert json.loads((tmp_path / "character-speed.json").read_text())["app_version"] == "1.0.1"


def test_a_corrupt_cache_is_measured_again(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DUBIFY_DATA_DIR", str(tmp_path))
    calls = counting(monkeypatch, 10.0)
    for junk in ("{not json", "[]", '{"app_version": "x"}'):
        (tmp_path / "character-speed.json").write_text(junk)
        monkeypatch.setattr(speed, "_memo", None)
        assert speed.face_step_enabled() is True
    assert len(calls) == 3
    assert json.loads((tmp_path / "character-speed.json").read_text())["faces"] is True


def test_without_a_data_folder_nothing_is_written_and_the_answer_stays_in_the_process(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "DUBIFY_DATA_DIR", "")
    calls = counting(monkeypatch, 10.0)
    assert speed.face_step_enabled() and speed.face_step_enabled()
    assert len(calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_a_failed_measurement_raises(monkeypatch):
    def broken():
        raise OSError("ffmpeg missing")

    monkeypatch.setattr(speed, "measure", broken)
    with pytest.raises(OSError):
        speed.face_step_enabled()


def test_faces_are_decided_inside_the_worker_thread(monkeypatch, tmp_path):
    seen: dict = {}

    def enabled():
        seen["thread"] = threading.current_thread()
        return False

    def fake_prints(video, vocals, spans, with_faces=True):
        seen["with_faces"] = with_faces
        return ["found" for _ in spans]

    monkeypatch.setattr(speed, "face_step_enabled", enabled)
    monkeypatch.setattr(prints_module, "prints_for_spans", fake_prints)
    monkeypatch.setattr(store, "resolve_media_path", lambda p: p)

    class Db:
        async def commit(self):
            pass

    job = SimpleNamespace(id="job-1234", video_path="v.mp4")
    found = asyncio.run(store._find_prints(Db(), job, [(0.0, 1.0)], tmp_path / "vocals.wav"))
    assert found == ["found"]
    assert seen["with_faces"] is False
    assert seen["thread"] is not threading.main_thread()
