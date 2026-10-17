"""
Desktop (Tauri sidecar) behaviour.

The desktop shell passes DUBIFY_* environment variables: where the user's
data lives, which app version this is, and a per-launch instance id. With
those set, the backend keeps its database, media and settings in that folder,
reads API keys from the user's own settings file (never from a file baked into
the binary), finds ffmpeg even when launched from Finder, proves its identity
on /health/ready, exposes a Settings API that never returns stored secrets,
and exits when the app that started it goes away.
"""
import os
import sys
import threading
from pathlib import Path

import pytest

from app.core import binaries, config
from app.core.config import settings as app_settings


def _fresh(monkeypatch, **env):
    """A Settings built the way the app builds it at import, from a clean environment."""
    for key in ("DATABASE_URL", "UPLOAD_DIR", "DUBIFY_DATA_DIR", "DUBIFY_APP_VERSION",
                "GEMINI_API_KEY", "HF_TOKEN", "VOXCPM2_API_URL"):   # conftest sets these
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return config.Settings(_env_file=config._env_file_path())


# ── Data location and settings file ──────────────────────────────────────────

def test_desktop_mode_keeps_database_and_media_in_the_app_data_folder(monkeypatch, tmp_path):
    s = _fresh(monkeypatch, DUBIFY_DATA_DIR=str(tmp_path))
    assert s.effective_db_url == f"sqlite+aiosqlite:///{tmp_path}/dubify.db"
    assert s.UPLOAD_DIR == str(tmp_path / "uploads")


def test_desktop_mode_reads_the_users_own_settings_file(monkeypatch, tmp_path):
    (tmp_path / "settings.env").write_text("GEMINI_API_KEY=from-settings\n")
    s = _fresh(monkeypatch, DUBIFY_DATA_DIR=str(tmp_path))
    assert s.GEMINI_API_KEY == "from-settings"


def test_a_frozen_build_never_reads_an_env_file_baked_into_the_binary(monkeypatch, tmp_path):
    meipass = tmp_path / "meipass"
    meipass.mkdir()
    (meipass / ".env").write_text("GEMINI_API_KEY=baked-in\n")
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    assert _fresh(monkeypatch, DUBIFY_DATA_DIR=str(data)).GEMINI_API_KEY != "baked-in"
    assert _fresh(monkeypatch).GEMINI_API_KEY != "baked-in"


def test_explicit_upload_dir_and_database_still_win(monkeypatch, tmp_path):
    s = _fresh(monkeypatch, DUBIFY_DATA_DIR=str(tmp_path), UPLOAD_DIR="/Volumes/Media/uploads",
               DATABASE_URL="postgresql+asyncpg://u:p@db.example/dubify")
    assert s.UPLOAD_DIR == "/Volumes/Media/uploads"
    assert s.effective_db_url.startswith("postgresql+asyncpg://")


def test_development_mode_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)      # no .env here
    s = _fresh(monkeypatch)
    assert s.UPLOAD_DIR == "./uploads"
    assert s.effective_db_url == "sqlite+aiosqlite:///./khmer_dubber_dev.db"


def test_version_comes_from_the_desktop_shell(monkeypatch, tmp_path):
    assert _fresh(monkeypatch, DUBIFY_DATA_DIR=str(tmp_path), DUBIFY_APP_VERSION="0.3.1").version == "0.3.1"


# ── ffmpeg discovery ──────────────────────────────────────────────────────────

def _fake_tool(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / name
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    return tool


def test_an_explicit_ffmpeg_path_is_used(monkeypatch, tmp_path):
    bundled = _fake_tool(tmp_path / "MacOS", "ffmpeg")
    monkeypatch.setattr(app_settings, "FFMPEG_PATH", str(bundled))
    assert binaries.ffmpeg() == str(bundled)


def test_homebrew_ffmpeg_is_found_without_the_shells_path(monkeypatch, tmp_path):
    """Apps opened from Finder get PATH=/usr/bin:/bin:/usr/sbin:/sbin — no Homebrew."""
    brew = _fake_tool(tmp_path / "homebrew" / "bin", "ffprobe")
    monkeypatch.setattr(app_settings, "FFPROBE_PATH", "")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(binaries, "EXTRA_SEARCH_DIRS", (str(brew.parent),))
    assert binaries.ffprobe() == str(brew)


def test_a_missing_ffmpeg_falls_back_to_the_bare_name(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "FFMPEG_PATH", "")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(binaries, "EXTRA_SEARCH_DIRS", ())
    assert binaries.ffmpeg() == "ffmpeg"
    assert binaries.find("ffmpeg") is None


# ── Readiness / identity ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ready_check_names_this_backend_instance(client, monkeypatch):
    monkeypatch.setattr(app_settings, "DUBIFY_INSTANCE_ID", "launch-42")
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["instance"] == "launch-42"
    assert resp.json()["status"] == "ok"


# ── Settings API ──────────────────────────────────────────────────────────────

@pytest.fixture
def desktop_settings(monkeypatch, tmp_path):
    """Desktop mode with every editable setting snapshotted, so writes don't leak between tests."""
    from app.api.routes import settings as settings_routes
    for field in settings_routes.EDITABLE:
        monkeypatch.setattr(app_settings, field, getattr(app_settings, field))
    monkeypatch.setattr(app_settings, "DUBIFY_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_settings_never_reveal_stored_secrets(client, monkeypatch, desktop_settings):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", "super-secret-value")
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert "super-secret-value" not in resp.text
    assert resp.json()["secrets"]["GEMINI_API_KEY"] is True
    assert resp.json()["editable"] is True


@pytest.mark.asyncio
async def test_saving_settings_persists_privately_and_applies_immediately(client, desktop_settings):
    resp = await client.put("/api/v1/settings", json={"GEMINI_API_KEY": "abc123", "TRANSLATION_BACKEND": "deep"})
    assert resp.status_code == 200
    stored = desktop_settings / "settings.env"
    assert "GEMINI_API_KEY=abc123" in stored.read_text()
    assert oct(stored.stat().st_mode & 0o777) == "0o600"
    assert app_settings.GEMINI_API_KEY == "abc123"          # no restart needed
    assert app_settings.TRANSLATION_BACKEND == "deep"
    assert "abc123" not in resp.text


@pytest.mark.asyncio
async def test_saved_settings_survive_a_restart(client, monkeypatch, desktop_settings):
    await client.put("/api/v1/settings", json={"VOXCPM2_API_URL": "https://voxcpm.example.com", "HF_TOKEN": 'hf "quoted"'})
    reloaded = _fresh(monkeypatch, DUBIFY_DATA_DIR=str(desktop_settings))
    assert reloaded.VOXCPM2_API_URL == "https://voxcpm.example.com"
    assert reloaded.HF_TOKEN == 'hf "quoted"'


@pytest.mark.asyncio
async def test_an_empty_secret_clears_it(client, monkeypatch, desktop_settings):
    await client.put("/api/v1/settings", json={"GEMINI_API_KEY": "abc123"})
    await client.put("/api/v1/settings", json={"GEMINI_API_KEY": ""})
    assert app_settings.GEMINI_API_KEY == ""
    assert "GEMINI_API_KEY=abc123" not in (desktop_settings / "settings.env").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"TRANSLATION_BACKEND": "babelfish"},
    {"GEMINI_API_KEY": "line1\nINJECTED=1"},
    {"VOXCPM2_API_URL": "file:///etc/passwd"},
    {"TTS_BATCH_CONCURRENCY": 99},
    {"DATABASE_URL": "postgresql://x"},        # not editable here
])
async def test_invalid_settings_are_rejected(client, desktop_settings, payload):
    resp = await client.put("/api/v1/settings", json=payload)
    assert resp.status_code == 422
    assert not (desktop_settings / "settings.env").exists()


@pytest.mark.asyncio
async def test_settings_are_read_only_during_development(client, monkeypatch, desktop_settings):
    monkeypatch.setattr(app_settings, "DUBIFY_DATA_DIR", "")
    assert (await client.get("/api/v1/settings")).json()["editable"] is False
    assert (await client.put("/api/v1/settings", json={"TRANSLATION_BACKEND": "deep"})).status_code == 409


# ── Exit with the parent app ──────────────────────────────────────────────────

def test_backend_exits_when_the_app_that_started_it_goes_away():
    """The desktop app holds the sidecar's stdin open; EOF means it quit or crashed."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from sidecar_entry import watch_parent

    read_fd, write_fd = os.pipe()
    gone = threading.Event()
    watch_parent(os.fdopen(read_fd, "rb"), gone.set)
    assert not gone.wait(0.2)
    os.close(write_fd)                   # the app went away
    assert gone.wait(2.0)


def test_backend_raises_the_open_file_limit_it_gets_from_finder():
    """An app opened from Finder may only have 256 files open; ffmpeg inherits the raised limit."""
    import resource
    import sidecar_entry

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
    try:
        assert sidecar_entry.raise_open_file_limit() >= 4096
        assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] >= 4096
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_self_test_reports_khmer_shaping(capsys):
    """The release build runs `movelo-backend --self-test` and stops if Khmer can't be shaped."""
    import sidecar_entry

    code = sidecar_entry.self_test()
    out = capsys.readouterr().out
    assert "backend imports: ok" in out and "Khmer text shaping:" in out
    assert code == (0 if "shaping: ok" in out else 1)
