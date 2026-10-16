"""
Settings → AI models: once a Gemini key is saved, each job (translation,
transcription, Assistant, voice fallback) offers the models that key can see,
and a chosen model can be checked with a real request — the model list alone
can't be trusted (retired models stay listed). "Automatic" (empty) means the
built-in choice, which replaces itself when Google retires it.
"""
import httpx
import pytest

from app.core.config import settings as app_settings
from app.services import gemini_models

LISTED = [
    "gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-flash-lite-latest",
    "gemini-3.6-flash", "gemini-3.8-flash", "gemini-flash-latest", "gemini-3-flash-preview",
    "gemini-2.5-flash-preview-tts", "gemini-3.8-flash-tts", "gemini-3.1-flash-image",
    "text-embedding-005",
]


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    from app.api.routes import settings as settings_routes
    for field in settings_routes.EDITABLE:
        monkeypatch.setattr(app_settings, field, getattr(app_settings, field))
    monkeypatch.setattr(app_settings, "DUBIFY_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", "test-key")
    gemini_models.reset()

    async def fake_list():
        return [{"name": f"models/{n}", "supportedGenerationMethods":
                 ["embedContent"] if "embedding" in n else ["generateContent"]} for n in LISTED]
    monkeypatch.setattr(gemini_models, "_list_models", fake_list)
    yield tmp_path
    gemini_models.reset()


def _choice(body, setting):
    return next(c for c in body["choices"] if c["setting"] == setting)


@pytest.mark.asyncio
async def test_each_job_lists_only_models_that_suit_it(client, desktop):
    resp = await client.get("/api/v1/settings/models")
    assert resp.status_code == 200
    body = resp.json()
    translation = _choice(body, "GEMINI_MODEL")["options"]
    assert translation[0] == "gemini-flash-lite-latest"            # the maintained alias comes first
    assert "gemini-3.5-flash-lite" in translation and "gemini-3.8-flash-tts" not in translation
    asr = _choice(body, "GEMINI_ASR_MODEL")["options"]
    assert asr.index("gemini-3.8-flash") < asr.index("gemini-3.6-flash")   # newest first
    assert "gemini-3-flash-preview" not in asr and "gemini-3.1-flash-image" not in asr
    tts = _choice(body, "GEMINI_TTS_MODEL")["options"]
    assert tts == ["gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"]


@pytest.mark.asyncio
async def test_the_list_says_when_a_retired_choice_was_replaced(client, desktop, monkeypatch):
    monkeypatch.setattr(app_settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
    await gemini_models.switch_model("translation", "gemini-2.5-flash-lite", "no longer available")
    choice = _choice((await client.get("/api/v1/settings/models")).json(), "GEMINI_MODEL")
    assert choice["value"] == "gemini-2.5-flash-lite"
    assert choice["in_use"] == "gemini-flash-lite-latest"


@pytest.mark.asyncio
async def test_choosing_models_needs_a_key_first(client, desktop, monkeypatch):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", "")
    resp = await client.get("/api/v1/settings/models")
    assert resp.status_code == 400
    assert "API key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_model_is_checked_with_a_real_request(client, desktop, monkeypatch):
    async def fake_post(url, payload, headers, timeout):
        model = url.split("/models/")[1].split(":")[0]
        if model == "gemini-2.5-flash-lite":
            return httpx.Response(404, json={"error": {"message": "This model models/gemini-2.5-flash-lite "
                                                       "is no longer available to new users."}},
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "OK"}]}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(gemini_models, "_post", fake_post)

    ok = await client.post("/api/v1/settings/models/test", json={"setting": "GEMINI_MODEL", "model": "gemini-3.5-flash-lite"})
    assert ok.json() == {"ok": True, "message": "gemini-3.5-flash-lite works with your key."}
    bad = await client.post("/api/v1/settings/models/test", json={"setting": "GEMINI_MODEL", "model": "gemini-2.5-flash-lite"})
    assert bad.json()["ok"] is False and "no longer available" in bad.json()["message"]
    nope = await client.post("/api/v1/settings/models/test", json={"setting": "HF_TOKEN", "model": "x"})
    assert nope.status_code == 422


@pytest.mark.asyncio
async def test_automatic_means_the_built_in_choice(client, desktop):
    resp = await client.put("/api/v1/settings", json={"GEMINI_MODEL": "", "GEMINI_TTS_MODEL": ""})
    assert resp.status_code == 200
    assert gemini_models.current_model("translation") == "gemini-flash-lite-latest"
    assert gemini_models.current_model("tts") == "gemini-2.5-flash-preview-tts"
    resp = await client.put("/api/v1/settings", json={"GEMINI_TTS_MODEL": "gemini-3.8-flash-tts"})
    assert gemini_models.current_model("tts") == "gemini-3.8-flash-tts"


@pytest.mark.asyncio
async def test_a_setting_never_changed_shows_as_automatic(client, desktop):
    choice = _choice((await client.get("/api/v1/settings/models")).json(), "GEMINI_ASR_MODEL")
    assert choice["value"] == "" and choice["automatic"] == choice["in_use"]
