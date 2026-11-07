"""Settings → several Gemini keys, and the Key Monitor routes (/gateway). The keys are made up; Google is a fake."""
import json

import httpx
import pytest

from app.core.config import settings as app_settings
from app.services import gemini_models, key_pool

pytestmark = pytest.mark.asyncio

K1, K2, K3 = ("AIzaSy" + c * 33 for c in "ABC")


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    from app.api.routes import settings as settings_routes
    for field in settings_routes.EDITABLE:
        monkeypatch.setattr(app_settings, field, getattr(app_settings, field))
    monkeypatch.setattr(app_settings, "DUBIFY_DATA_DIR", str(tmp_path))
    return tmp_path


class FakeGoogle:
    def __init__(self):
        self.keys: list[str] = []
        self.reply = None

    async def __call__(self, url, payload, headers, timeout):
        self.keys.append(headers["x-goog-api-key"])
        assert "key=" not in url                              # keys travel in a header, never the URL
        if self.reply:
            return self.reply(payload)
        lines = json.loads(payload["contents"][0]["parts"][0]["text"]) if payload.get("system_instruction") else []
        out = [{"index": l["index"], "text": "km:" + l["text"]} for l in lines]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(out)}]}}],
                                         "usageMetadata": {"totalTokenCount": 50}})


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(gemini_models, "_post", fake)
    return fake


async def test_several_keys_are_saved_as_one_line_and_counted(client, desktop):
    text = f"api_key01={K1}\napi_key02 = {K2}\n\n{K3}\n{K1}\n"
    resp = await client.put("/api/v1/settings", json={"GEMINI_API_KEY": text})
    assert resp.status_code == 200
    assert resp.json()["secrets"]["gemini_keys_count"] == 3 and resp.json()["secrets"]["GEMINI_API_KEY"] is True
    assert app_settings.parsed_gemini_keys() == [K1, K2, K3]
    stored = (desktop / "settings.env").read_text()
    assert f"GEMINI_API_KEY={K1},{K2},{K3}" in stored
    assert K1 not in resp.text


async def test_a_bad_key_line_is_named_by_number_and_never_echoed(client, desktop):
    resp = await client.put("/api/v1/settings", json={"GEMINI_API_KEY": f"{K1}\nthis is not a key!\n{K2}"})
    assert resp.status_code == 422
    assert "key 2" in resp.text and "this is not a key" not in resp.text and K1 not in resp.text


async def test_more_than_fifty_keys_are_refused(client, desktop):
    many = "\n".join(f"AIzaSy{i:033d}" for i in range(51))
    assert (await client.put("/api/v1/settings", json={"GEMINI_API_KEY": many})).status_code == 422


async def test_an_empty_value_removes_every_key(client, desktop, monkeypatch):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", f"{K1},{K2}")
    resp = await client.put("/api/v1/settings", json={"GEMINI_API_KEY": ""})
    assert resp.status_code == 200 and resp.json()["secrets"]["gemini_keys_count"] == 0


async def test_the_monitor_page_and_stats_never_show_a_key(client, monkeypatch, google):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", f"{K1}\n{K2}")
    page = await client.get("/gateway")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    stats = await client.get("/gateway/stats")
    assert stats.status_code == 200 and stats.json()["totals"]["total_keys"] == 2
    assert K1 not in stats.text + page.text and K2 not in stats.text + page.text


async def test_a_ping_uses_that_key_and_reports_latency(client, monkeypatch, google):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", f"{K1}\n{K2}")
    await client.get("/gateway/stats")
    resp = await client.post("/gateway/keys/key_2/test")
    assert resp.json()["ok"] is True and "latency_ms" in resp.json() and google.keys == [K2]
    assert (await client.post("/gateway/keys/nope/test")).status_code == 404


async def test_translate_rotates_over_the_keys_and_returns_every_index(client, monkeypatch, google):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", f"{K1}\n{K2}\n{K3}")
    for batch in range(3):
        body = {"target_lang": "km", "segments": [{"index": i, "text": f"line {batch}-{i}"} for i in range(3)]}
        resp = await client.post("/gateway/translate?wait=true", json=body)
        data = resp.json()
        assert resp.status_code == 200 and data["status"] == "done" and data["failed_indices"] == []
        assert [s["text"] for s in data["segments"]] == [f"km:line {batch}-{i}" for i in range(3)]
    assert sorted(google.keys) == sorted([K1, K2, K3])          # each key took one request


async def test_translate_wants_contiguous_indices(client, monkeypatch, google):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", K1)
    body = {"target_lang": "km", "segments": [{"index": 0, "text": "a"}, {"index": 2, "text": "b"}]}
    assert (await client.post("/gateway/translate", json=body)).status_code == 422
    too_many = {"target_lang": "km", "segments": [{"index": i, "text": "x"} for i in range(221)]}
    assert (await client.post("/gateway/translate", json=too_many)).status_code == 422


async def test_a_chunk_with_a_missing_line_is_halved_and_repaired(client, monkeypatch, google):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", K1)
    calls = {"n": 0}
    original = google.__call__

    async def flaky(url, payload, headers, timeout):
        calls["n"] += 1
        lines = json.loads(payload["contents"][0]["parts"][0]["text"])
        if calls["n"] == 1:
            lines = lines[:-1]                                   # the first answer drops a line
        out = [{"index": l["index"], "text": "km:" + l["text"]} for l in lines]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(out)}]}}]})
    monkeypatch.setattr(gemini_models, "_post", flaky)
    body = {"target_lang": "km", "segments": [{"index": i, "text": f"t{i}"} for i in range(4)]}
    data = (await client.post("/gateway/translate", json=body)).json()
    assert data["status"] == "done" and calls["n"] == 3


async def test_chat_completions_validates_the_model_and_answers(client, monkeypatch, google):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", K1)
    google.reply = lambda payload: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": "hello"}]}}], "usageMetadata": {"totalTokenCount": 7}})
    bad = await client.post("/gateway/v1/chat/completions", json={"model": "a/b?x=1", "messages": []})
    assert bad.status_code == 422
    ok = await client.post("/gateway/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert ok.status_code == 200 and ok.json()["choices"][0]["message"]["content"] == "hello"


async def test_other_websites_cannot_spend_the_keys(client, monkeypatch, google):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", K1)
    await client.get("/gateway/stats")
    for headers in ({"sec-fetch-site": "cross-site"}, {"origin": "https://evil.example"}):
        assert (await client.post("/gateway/keys/key_1/test", headers=headers)).status_code == 403
    assert google.keys == []
    assert (await client.post("/gateway/keys/key_1/test", headers={"sec-fetch-site": "same-origin"})).json()["ok"]


async def test_a_ping_counts_against_the_keys_caps(client, monkeypatch, google):
    monkeypatch.setattr(app_settings, "GEMINI_API_KEY", K1)
    monkeypatch.setattr(app_settings, "GEMINI_RPM_PER_KEY", 1)
    assert (await client.post("/gateway/keys/key_1/test")).json()["ok"] is True
    again = (await client.post("/gateway/keys/key_1/test")).json()
    assert again["ok"] is False and len(google.keys) == 1
