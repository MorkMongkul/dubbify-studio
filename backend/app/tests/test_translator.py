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
        return ["សួស្តី", "你好"]              # second line came back untranslated

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
