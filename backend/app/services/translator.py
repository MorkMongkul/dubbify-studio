"""
app/services/translator.py
Translation using Google Gemini API with full conversation context.

Gemini sees the ENTIRE conversation before translating each line —
this produces natural, emotional, dubbing-quality translations instead
of robotic literal ones.

Also fixes the pyannoteAI spacing issue:
  "我 回 来了" → "我回来了" before translating

Backends (set TRANSLATION_BACKEND in .env):
  "gemini"  → Gemini 2.5 Flash with context (best quality, recommended)
  "deep"    → Google Translate via deep-translator (fast, no context)

Get your Gemini API key at: aistudio.google.com
"""
import logging
import asyncio
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

# Language names for the prompt
LANG_NAMES = {
    "zh": "Chinese (Mandarin)",
    "en": "English",
    "km": "Khmer (Cambodian)",
    "ko": "Korean",
    "ja": "Japanese",
    "th": "Thai",
    "vi": "Vietnamese",
    "fr": "French",
    "de": "German",
}

# Google Translate language codes for deep-translator fallback
GOOGLE_LANG_MAP = {
    "zh": "zh-CN", "en": "en", "km": "km",
    "ko": "ko", "ja": "ja", "th": "th",
    "vi": "vi", "fr": "fr", "de": "de",
}


def _clean_chinese(text: str) -> str:
    """
    Remove spaces between Chinese characters added by pyannoteAI ASR.
    "我 回 来了" → "我回来了"
    Preserves spaces between Latin words if mixed content.
    """
    import re
    # Remove spaces between CJK characters
    cleaned = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)
    # Remove leading/trailing spaces
    return cleaned.strip()


def _build_gemini_prompt(
    segments: list,
    current_index: int,
    source_lang: str,
    target_lang: str,
) -> str:
    """
    Build a context-aware prompt for Gemini.

    Sends the full conversation so far + current line.
    Gemini translates with emotional and tonal awareness.
    """
    src_name = LANG_NAMES.get(source_lang, source_lang)
    tgt_name = LANG_NAMES.get(target_lang, target_lang)

    # Build conversation context (previous lines)
    context_lines = []
    for i, seg in enumerate(segments[:current_index]):
        if seg.get("source_text", "").strip():
            speaker = seg.get("speaker_label", f"SPEAKER_{i:02d}")
            text    = _clean_chinese(seg["source_text"])
            context_lines.append(f"  {speaker}: {text}")

    context_block = "\n".join(context_lines[-10:]) if context_lines else "  (start of scene)"

    current_seg    = segments[current_index]
    current_text   = _clean_chinese(current_seg.get("source_text", ""))
    current_speaker = current_seg.get("speaker_label", "SPEAKER_00")

    prompt = f"""You are a professional dubbing translator for {src_name} drama/film content.
Translate the CURRENT LINE from {src_name} to {tgt_name}.

Rules:
- Natural spoken language, NOT literal word-for-word translation
- Match the emotion and tone of the original (angry, tender, formal, casual etc.)
- Keep it concise — dubbing must fit the original speaking duration
- Use natural {tgt_name} expressions that a native speaker would say
- Do NOT add explanations, notes, or punctuation beyond what's natural
- Return ONLY the translated text, nothing else

CONVERSATION CONTEXT (previous lines):
{context_block}

CURRENT LINE to translate ({current_speaker}):
{current_text}

{tgt_name} translation:"""

    return prompt


async def _translate_with_gemini(
    text: str,
    source_lang: str,
    target_lang: str,
    context_segments: list = None,
    current_index: int = 0,
) -> str:
    """
    Translate a single line using Gemini 2.5 Flash with conversation context.
    """
    if not text.strip():
        return ""

    clean_text = _clean_chinese(text)
    if not clean_text:
        return ""

    # Build context-aware prompt if segments provided
    if context_segments and current_index < len(context_segments):
        prompt = _build_gemini_prompt(
            context_segments, current_index, source_lang, target_lang
        )
    else:
        # Simple prompt without context (for single-line calls)
        src_name = LANG_NAMES.get(source_lang, source_lang)
        tgt_name = LANG_NAMES.get(target_lang, target_lang)
        prompt = (
            f"Translate this {src_name} text to natural spoken {tgt_name} "
            f"suitable for voice dubbing. Return only the translation:\n{clean_text}"
        )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": settings.GEMINI_API_KEY,
    }

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,      # low = consistent, not too creative
            "maxOutputTokens": 512,
        },
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        resp = await client.post(GEMINI_API_URL, json=payload, headers=headers)

    if resp.status_code == 429:
        logger.warning("Gemini rate limit — waiting 5s...")
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(GEMINI_API_URL, json=payload, headers=headers)

    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    try:
        result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return result
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response format: {data}")


def _translate_with_deep(text: str, source_lang: str, target_lang: str) -> str:
    """Fallback: Google Translate via deep-translator. Fast but no context."""
    try:
        from deep_translator import GoogleTranslator
        clean = _clean_chinese(text)
        src = GOOGLE_LANG_MAP.get(source_lang, source_lang)
        tgt = GOOGLE_LANG_MAP.get(target_lang, target_lang)
        return GoogleTranslator(source=src, target=tgt).translate(clean) or text
    except Exception as e:
        logger.error(f"deep-translator failed: {e}")
        return text


# ── Main entry points ──────────────────────────────────────────

async def translate_text(
    text: str,
    source_lang: str = "zh",
    target_lang: str = "km",
) -> str:
    """Translate a single text string (no context)."""
    if not text.strip():
        return ""

    backend = settings.TRANSLATION_BACKEND.lower()

    if backend == "gemini" and settings.GEMINI_API_KEY:
        return await _translate_with_gemini(text, source_lang, target_lang)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _translate_with_deep, text, source_lang, target_lang
    )


async def translate_batch(
    texts: list[str],
    source_lang: str = "zh",
    target_lang: str = "km",
    segments_context: list = None,
) -> list[str]:
    """
    Translate a batch of texts.

    When backend=gemini and segments_context is provided,
    each line is translated with full conversation context —
    producing natural, emotional dubbing-quality translations.
    """
    if not texts:
        return []

    backend = settings.TRANSLATION_BACKEND.lower()

    if backend == "gemini" and settings.GEMINI_API_KEY:
        # Translate sequentially with context (each line sees previous lines)
        # Semaphore prevents hitting rate limits
        sem = asyncio.Semaphore(3)

        async def translate_one(i: int, text: str) -> str:
            if not text.strip():
                return ""
            async with sem:
                try:
                    return await _translate_with_gemini(
                        text=text,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        context_segments=segments_context,
                        current_index=i,
                    )
                except Exception as e:
                    logger.warning(f"Gemini failed for segment {i}: {e} — using deep-translator")
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(
                        None, _translate_with_deep, text, source_lang, target_lang
                    )

        tasks = [translate_one(i, t) for i, t in enumerate(texts)]
        return await asyncio.gather(*tasks)

    # Fallback: deep-translator (no context, fast)
    max_concurrent = 4
    sem = asyncio.Semaphore(max_concurrent)

    async def translate_one_deep(text: str) -> str:
        async with sem:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, _translate_with_deep, text, source_lang, target_lang
            )

    return await asyncio.gather(*[translate_one_deep(t) for t in texts])