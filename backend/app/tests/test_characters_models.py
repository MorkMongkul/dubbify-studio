"""The four character models ship with the app: right files, right bytes, and they load and run."""
import hashlib
import json

import pytest

from app.services.characters import models

NEEDS_FETCH = "run scripts/fetch-models.sh"


def _manifest():
    return json.loads(models.MANIFEST.read_text())


def test_every_model_matches_its_manifest():
    entries = _manifest()
    assert {e["name"] for e in entries} == {"eres2netv2", "sface", "yunet", "lightasd"}
    for entry in entries:
        file = models.DIR / entry["file"]
        if not file.exists():
            pytest.skip(NEEDS_FETCH)
        assert file.stat().st_size == entry["bytes"], entry["name"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == entry["sha256"], entry["name"]


def test_a_missing_model_says_how_to_get_it(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "DIR", tmp_path)
    with pytest.raises(models.ModelMissing, match="scripts/fetch-models.sh"):
        models.path("sface")
    with pytest.raises(KeyError):
        models.path("nope")


def test_the_models_load_and_run():
    if not all((models.DIR / e["file"]).exists() for e in _manifest()):
        pytest.skip(NEEDS_FETCH)
    assert models.self_test() == "Character models: ok"
