"""
resolve_media_path must survive media folders moved between machines: rows in
a shared DB hold absolute paths from whichever machine created them.
"""
from app.core import paths as paths_module
from app.core.paths import resolve_media_path


def test_existing_absolute_path_unchanged(tmp_path):
    f = tmp_path / "clip.wav"
    f.write_bytes(b"x")
    assert resolve_media_path(str(f)) == str(f)


def test_foreign_absolute_path_remaps_to_local_upload_root(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    (upload_root / "voices" / "abc").mkdir(parents=True)
    local_file = upload_root / "voices" / "abc" / "ref.wav"
    local_file.write_bytes(b"x")
    monkeypatch.setattr(paths_module.settings, "UPLOAD_DIR", str(upload_root))

    foreign = "/Users/othermachine/Documents/dubbify-studio/backend/uploads/voices/abc/ref.wav"
    assert resolve_media_path(foreign) == str(local_file)


def test_missing_foreign_path_returned_as_is(tmp_path, monkeypatch):
    monkeypatch.setattr(paths_module.settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    foreign = "/Users/othermachine/backend/uploads/voices/nope/ref.wav"
    assert resolve_media_path(foreign) == foreign
