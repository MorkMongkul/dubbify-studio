"""
Google retires Gemini model versions on its own schedule. A retired model
answers generateContent with 404 "This model ... is no longer available to new
users" — and can still show up in the model list. Every feature must move on to
a working model by itself instead of failing, and remember the switch.
"""
import httpx
import pytest

from app.services import gemini_models

RETIRED = (
    '{"error": {"code": 404, "message": "This model models/gemini-2.5-flash-lite is no longer '
    'available to new users. Please update your code to use models/gemini-3.5-flash-lite", '
    '"status": "NOT_FOUND"}}'
)
OK = '{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}'


def _response(status: int, body: str) -> httpx.Response:
    return httpx.Response(status, text=body, request=httpx.Request("POST", "https://example.invalid"))


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    gemini_models.reset()
    monkeypatch.setattr(gemini_models.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini_models.settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")
    monkeypatch.setattr(gemini_models.settings, "GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    yield
    gemini_models.reset()


@pytest.fixture
def api(monkeypatch):
    """Fake generateContent: `retired` models answer 404, everything else 200."""
    class Api:
        retired: set = set()
        calls: list = []
        listed: list = []

    Api.retired, Api.calls, Api.listed = set(), [], []

    async def fake_post(url, payload, headers, timeout):
        model = url.split("/models/")[1].split(":")[0]
        Api.calls.append(model)
        return _response(404, RETIRED) if model in Api.retired else _response(200, OK)

    async def fake_list():
        return [{"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]} for n in Api.listed]

    monkeypatch.setattr(gemini_models, "_post", fake_post)
    monkeypatch.setattr(gemini_models, "_list_models", fake_list)
    return Api


@pytest.mark.parametrize("status, body, expected", [
    (404, RETIRED, True),
    (404, "{}", True),
    (400, '{"error": {"message": "models/foo is not found for API version v1beta"}}', True),
    (400, '{"error": {"message": "* GenerateContentRequest.contents: contents is not specified"}}', False),
    (429, '{"error": {"message": "Resource has been exhausted"}}', False),
    (500, "{}", False),
])
def test_tells_a_retired_model_from_other_errors(status, body, expected):
    assert gemini_models.is_model_unavailable(status, body) is expected


@pytest.mark.asyncio
async def test_a_retired_model_is_replaced_by_googles_maintained_alias(api):
    api.retired = {"gemini-2.5-flash-lite"}
    resp = await gemini_models.generate_content("translation", {}, timeout=10)
    assert resp.status_code == 200
    assert api.calls == ["gemini-2.5-flash-lite", "gemini-flash-lite-latest"]


@pytest.mark.asyncio
async def test_the_switch_is_remembered_for_the_session(api):
    api.retired = {"gemini-2.5-flash-lite"}
    await gemini_models.generate_content("translation", {}, timeout=10)
    api.calls.clear()
    await gemini_models.generate_content("translation", {}, timeout=10)
    assert api.calls == ["gemini-flash-lite-latest"]
    assert gemini_models.current_model("translation") == "gemini-flash-lite-latest"


@pytest.mark.asyncio
async def test_when_every_known_name_is_gone_the_newest_listed_model_of_the_same_kind_is_used(api):
    api.retired = {"gemini-2.5-flash-lite", "gemini-flash-lite-latest", "gemini-flash-latest"}
    api.listed = [
        "gemini-2.5-flash-lite",          # retired, though still listed
        "gemini-9.1-flash-lite",
        "gemini-9.2-flash-lite-preview",  # previews and other kinds don't qualify
        "gemini-9.5-flash",
        "gemini-9.0-flash-lite",
    ]
    resp = await gemini_models.generate_content("translation", {}, timeout=10)
    assert resp.status_code == 200
    assert api.calls[-1] == "gemini-9.1-flash-lite"


@pytest.mark.asyncio
async def test_with_nothing_left_the_last_answer_is_returned_for_the_caller_to_report(api):
    api.retired = {"gemini-2.5-flash-lite", "gemini-flash-lite-latest", "gemini-flash-latest"}
    resp = await gemini_models.generate_content("translation", {}, timeout=10)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_other_errors_are_returned_untouched_without_switching(api, monkeypatch):
    async def overloaded(url, payload, headers, timeout):
        api.calls.append(url)
        return _response(503, '{"error": {"message": "The model is overloaded"}}')
    monkeypatch.setattr(gemini_models, "_post", overloaded)
    resp = await gemini_models.generate_content("translation", {}, timeout=10)
    assert resp.status_code == 503
    assert len(api.calls) == 1
    assert gemini_models.current_model("translation") == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_changing_the_model_in_settings_starts_fresh(api, monkeypatch):
    api.retired = {"gemini-2.5-flash-lite"}
    await gemini_models.generate_content("translation", {}, timeout=10)
    monkeypatch.setattr(gemini_models.settings, "GEMINI_MODEL", "gemini-3.5-flash-lite")
    assert gemini_models.current_model("translation") == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_voice_models_fall_back_to_the_newest_listed_tts_model(api):
    api.retired = {"gemini-2.5-flash-preview-tts"}
    api.listed = ["gemini-3.8-flash", "gemini-3.1-flash-tts-preview", "gemini-3.8-flash-tts"]
    await gemini_models.generate_content("tts", {}, timeout=10)
    assert api.calls[-1] == "gemini-3.8-flash-tts"
