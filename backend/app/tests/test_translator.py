"""
Translation must never pass the source language off as a translation:
a failed line is left empty (and counted), a line the no-context fallback
had to translate is counted, and the counts reach the job as a warning.
"""
import deep_translator
import pytest

from app.services import translator


class _FakeGoogle:
    """deep_translator.GoogleTranslator stand-in: behaviour set per test."""
    reply = None
    error = None

    def __init__(self, source, target):
        pass

    def translate(self, text):
        if _FakeGoogle.error:
            raise _FakeGoogle.error
        return _FakeGoogle.reply(text) if callable(_FakeGoogle.reply) else _FakeGoogle.reply


@pytest.fixture
def google(monkeypatch):
    _FakeGoogle.reply, _FakeGoogle.error = None, None
    monkeypatch.setattr(deep_translator, "GoogleTranslator", _FakeGoogle)
    return _FakeGoogle


@pytest.fixture
def gemini_backend(monkeypatch):
    monkeypatch.setattr(translator.settings, "TRANSLATION_BACKEND", "gemini")
    monkeypatch.setattr(translator.settings, "GEMINI_API_KEY", "test-key")

    async def no_sleep(_s):
        return None
    monkeypatch.setattr(translator.asyncio, "sleep", no_sleep)


def test_fallback_failure_returns_empty_not_the_source_text(google):
    google.error = RuntimeError("network down")
    assert translator._translate_with_deep("你好", "zh", "km") == ""


def test_fallback_output_still_in_chinese_counts_as_untranslated(google):
    google.reply = lambda text: text          # Google echoed the input back
    assert translator._translate_with_deep("你好", "zh", "km") == ""


def test_fallback_success_is_returned(google):
    google.reply = "សួស្តី"
    assert translator._translate_with_deep("你好", "zh", "km") == "សួស្តី"


@pytest.mark.asyncio
async def test_report_counts_fallback_and_failed_lines(monkeypatch, google, gemini_backend):
    async def gemini_down(*args, **kwargs):
        raise RuntimeError("Gemini batch error 500")

    monkeypatch.setattr(translator, "_translate_batch_with_gemini", gemini_down)
    google.reply = lambda text: "ខ្មែរ" if text == "第一句" else None   # second line fails too
    texts, report = await translator.translate_batch_with_report(["第一句", "第二句"], "zh", "km")
    assert texts == ["ខ្មែរ", ""]
    assert (report.fallback, report.failed) == (1, 1)


@pytest.mark.asyncio
async def test_gemini_lines_left_in_chinese_are_retried_with_the_fallback(monkeypatch, google, gemini_backend):
    async def gemini_partial(chunk, *args, **kwargs):
        # "你好" keeps coming back untranslated, even when asked again on its own
        return ["សួស្តី" if text == "早上好" else text for text in chunk]

    monkeypatch.setattr(translator, "_translate_batch_with_gemini", gemini_partial)
    google.reply = "ជម្រាបសួរ"
    texts, report = await translator.translate_batch_with_report(["早上好", "你好"], "zh", "km")
    assert texts == ["សួស្តី", "ជម្រាបសួរ"]
    assert (report.fallback, report.failed) == (1, 0)


@pytest.mark.asyncio
async def test_empty_source_lines_are_not_failures(monkeypatch, google, gemini_backend):
    async def gemini_ok(chunk, *args, **kwargs):
        return ["សួស្តី" if t else "" for t in chunk]

    monkeypatch.setattr(translator, "_translate_batch_with_gemini", gemini_ok)
    texts, report = await translator.translate_batch_with_report(["你好", ""], "zh", "km")
    assert texts == ["សួស្តី", ""]
    assert (report.fallback, report.failed) == (0, 0)


@pytest.mark.asyncio
async def test_translate_batch_keeps_its_plain_list_contract(monkeypatch, google, gemini_backend):
    async def gemini_ok(chunk, *args, **kwargs):
        return ["សួស្តី" for _ in chunk]

    monkeypatch.setattr(translator, "_translate_batch_with_gemini", gemini_ok)
    assert await translator.translate_batch(["你好"], "zh", "km") == ["សួស្តី"]


# ── Gemini through the real HTTP path (gemini_models._post is the boundary) ──

import httpx

from app.services import gemini_models

RETIRED_404 = (
    '{"error": {"code": 404, "message": "This model models/gemini-2.5-flash-lite is no longer '
    'available to new users.", "status": "NOT_FOUND"}}'
)


def _gemini_reply(lines):
    text = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]},
                          request=httpx.Request("POST", "https://example.invalid"))


def _error(status, body):
    return httpx.Response(status, text=body, request=httpx.Request("POST", "https://example.invalid"))


@pytest.fixture
def gemini_http(monkeypatch, gemini_backend):
    """Scripted generateContent: a list of responses per call, plus the models asked for."""
    gemini_models.reset()
    monkeypatch.setattr(translator.settings, "GEMINI_MODEL", "gemini-2.5-flash-lite")

    class Http:
        script = []
        models = []
        prompts = []

    Http.script, Http.models, Http.prompts = [], [], []

    async def fake_post(url, payload, headers, timeout):
        Http.models.append(url.split("/models/")[1].split(":")[0])
        Http.prompts.append(payload["contents"][0]["parts"][0]["text"])
        reply = Http.script.pop(0)
        return reply(url) if callable(reply) else reply

    monkeypatch.setattr(gemini_models, "_post", fake_post)
    yield Http
    gemini_models.reset()


@pytest.fixture
def no_deep_wait(monkeypatch):
    waits = []
    monkeypatch.setattr(translator, "_deep_sleep", waits.append)
    return waits


@pytest.mark.asyncio
async def test_a_retired_translation_model_is_replaced_not_sent_to_google_translate(gemini_http, google):
    """The 16 Oct failure: gemini-2.5-flash-lite answered 404 and every line ended up empty."""
    google.error = AssertionError("Google Translate must not be used when Gemini works")
    gemini_http.script = [_error(404, RETIRED_404), _gemini_reply(["សួស្តី", "អរគុណ"])]
    texts, report = await translator.translate_batch_with_report(["你好", "谢谢"], "zh", "km")
    assert texts == ["សួស្តី", "អរគុណ"]
    assert (report.fallback, report.failed) == (0, 0)
    assert gemini_http.models == ["gemini-2.5-flash-lite", "gemini-flash-lite-latest"]


@pytest.mark.asyncio
async def test_temporary_gemini_errors_are_retried_before_any_fallback(gemini_http, google):
    google.error = AssertionError("Google Translate must not be used for a temporary error")
    gemini_http.script = [
        _error(503, '{"error": {"message": "The model is overloaded"}}'),
        _error(429, '{"error": {"message": "Resource has been exhausted (e.g. check quota)."}}'),
        _gemini_reply(["សួស្តី"]),
    ]
    texts, report = await translator.translate_batch_with_report(["你好"], "zh", "km")
    assert texts == ["សួស្តី"]
    assert (report.fallback, report.failed) == (0, 0)


@pytest.mark.asyncio
async def test_lines_missing_from_geminis_answer_are_asked_for_again(gemini_http, google):
    google.error = AssertionError("Google Translate must not be needed")
    gemini_http.script = [_gemini_reply(["សួស្តី"]), _gemini_reply(["អរគុណ"])]   # first answer was cut short
    texts, report = await translator.translate_batch_with_report(["你好", "谢谢"], "zh", "km")
    assert texts == ["សួស្តី", "អរគុណ"]
    assert (report.fallback, report.failed) == (0, 0)
    assert "谢谢" in gemini_http.prompts[1] and "你好" not in gemini_http.prompts[1]


@pytest.mark.asyncio
async def test_a_warning_says_why_gemini_wasnt_used(gemini_http, google, no_deep_wait):
    gemini_http.script = [_error(400, '{"error": {"message": "API key not valid. Please pass a valid API key."}}')]
    google.reply = lambda text: None
    _texts, report = await translator.translate_batch_with_report(["你好"], "zh", "km")
    assert report.failed == 1
    assert "API key not valid" in report.warning()


def test_google_translate_fallback_sends_lines_together_and_retries_when_rate_limited(google, no_deep_wait):
    calls = []

    def reply(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("Server Error: You made too many requests to the server.")
        return "\n".join(f"ខ្មែរ{i}" for i, _ in enumerate(text.split("\n")))

    google.reply = reply
    lines = [f"第{i}句" for i in range(25)]
    results = translator._deep_translate_many(lines, "zh", "km")
    assert results == [f"ខ្មែរ{i}" for i in range(20)] + [f"ខ្មែរ{i}" for i in range(5)]
    assert len(calls) == 3            # 25 lines: one rate-limited try, then two batched requests
    assert any(w >= 1 for w in no_deep_wait)   # it backed off before retrying


def test_a_fallback_batch_that_comes_back_misaligned_is_redone_line_by_line(google, no_deep_wait):
    def reply(text):
        return "ខ្មែរ" if "\n" not in text else "ខ្មែរ ខ្មែរ"   # two lines merged into one
    google.reply = reply
    assert translator._deep_translate_many(["你好", "谢谢"], "zh", "km") == ["ខ្មែរ", "ខ្មែរ"]
