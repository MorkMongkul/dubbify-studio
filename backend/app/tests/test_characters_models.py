"""The four character models ship with the app: right files, right bytes, and they load and run."""
import hashlib
import json
import re

import pytest

from app.services.characters import models, store

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


def test_every_fetched_model_comes_from_a_fixed_version():
    """A branch (opencv_zoo's main) can change under the build, and the SHA-256 would then stop the release; a commit
    or a release's asset names one file for good."""
    for entry in _manifest():
        if entry["url"]:   # an empty URL: committed to the repository
            assert re.search(r"/[0-9a-f]{40}/", entry["url"]) or "/releases/download/" in entry["url"], entry["name"]


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


@pytest.fixture
def manifest_copy(monkeypatch, tmp_path):
    """models.json copied where a test may edit it, model_version read from the copy."""
    copy = tmp_path / "models.json"
    copy.write_text(models.MANIFEST.read_text())
    monkeypatch.setattr(models, "MANIFEST", copy)
    store.model_version.cache_clear()
    yield copy
    store.model_version.cache_clear()


def _edited(copy, name, **fields):
    entries = json.loads(copy.read_text())
    next(e for e in entries if e["name"] == name).update(fields)
    copy.write_text(json.dumps(entries))


def test_a_url_licence_or_layout_edit_keeps_the_model_version(manifest_copy):
    """Ruling AM: a version change re-makes every print (and nothing is sure until that ends), so only what makes
    the prints may change it."""
    before = store.model_version()
    _edited(manifest_copy, "sface", url="https://example.com/sface.onnx", licence="other", bytes=1)
    store.model_version.cache_clear()
    assert store.model_version() == before
    manifest_copy.write_text(json.dumps(list(reversed(json.loads(manifest_copy.read_text()))), indent=4))
    store.model_version.cache_clear()
    assert store.model_version() == before


def test_another_model_or_another_way_of_printing_changes_the_model_version(monkeypatch, manifest_copy):
    shipped, before = manifest_copy.read_text(), store.model_version()
    _edited(manifest_copy, "sface", sha256="0" * 64)
    store.model_version.cache_clear()
    assert store.model_version() != before and len(store.model_version()) == 12
    manifest_copy.write_text(shipped)
    monkeypatch.setattr(store, "PRINTS_VERSION", store.PRINTS_VERSION + 1)
    store.model_version.cache_clear()
    assert store.model_version() != before

