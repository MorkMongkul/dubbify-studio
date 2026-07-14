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

def _gemini_url() -> str:
    """Build Gemini API URL using model from settings (configurable via GEMINI_MODEL in .env)."""
    model = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
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


def _is_billing_error(err_str: str) -> bool:
    """
    Distinguish a 'depleted credits / billing' 429 from a transient rate-limit 429.
    Gemini returns 429 for both; billing errors will never succeed on retry, so we
    skip the 60s backoff and fall back to deep-translator immediately.
    """
    s = err_str.lower()
    return "resource_exhausted" in s and any(
        k in s for k in ("credit", "billing", "depleted", "prepay")
    )


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
    Build a dubbing-quality prompt for Gemini.
    Instructs Gemini to think like a voice actor, not a translator.
    """
    src_name = LANG_NAMES.get(source_lang, source_lang)
    tgt_name = LANG_NAMES.get(target_lang, target_lang)

    # Build conversation context (up to 12 previous lines)
    context_lines = []
    for i, seg in enumerate(segments[:current_index]):
        text = _clean_chinese(seg.get("source_text", "")).strip()
        if text:
            speaker = seg.get("speaker_label", f"SPEAKER_{i:02d}")
            context_lines.append(f"  [{speaker}]: {text}")

    context_block = (
        "\n".join(context_lines[-12:]) if context_lines
        else "  (opening scene)"
    )

    current_seg     = segments[current_index]
    current_text    = _clean_chinese(current_seg.get("source_text", ""))
    current_speaker = current_seg.get("speaker_label", "SPEAKER_00")

    prompt = f"""You are a professional voice dubbing scriptwriter specializing in {src_name} drama/film.
Your job is to write the {tgt_name} SPOKEN LINE for a voice actor to perform — not a subtitle translation.

CRITICAL RULES for dubbing scripts:
- Write how a REAL PERSON SPEAKS, not how a book is written
- Use SHORT, PUNCHY sentences — voice actors need to breathe
- Match the EMOTION: angry lines sound angry, tender lines sound tender, funny lines sound funny
- Use NATURAL EVERYDAY {tgt_name} — the kind people actually say out loud
- NEVER use formal/literary vocabulary when casual words exist
- Keep roughly the SAME LENGTH as the original — the actor must sync to the video
- NO stage directions, NO explanations, NO quotation marks — just the spoken words
- If the line is very short (one word, exclamation), keep it very short

SCENE CONTEXT (what was said before):
{context_block}

LINE TO ADAPT for voice actor [{current_speaker}]:
{current_text}

Write ONLY the {tgt_name} spoken line, nothing else:"""

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

    if context_segments and current_index < len(context_segments):
        prompt = _build_gemini_prompt(
            context_segments, current_index, source_lang, target_lang
        )
    else:
        src_name = LANG_NAMES.get(source_lang, source_lang)
        tgt_name = LANG_NAMES.get(target_lang, target_lang)
        prompt = (
            f"You are a dubbing scriptwriter. Write the natural spoken {tgt_name} "
            f"version of this {src_name} line for a voice actor. "
            f"Use casual everyday speech, match the emotion, keep it short. "
            f"Return ONLY the spoken line:\n{clean_text}"
        )

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": settings.GEMINI_API_KEY,
    }

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,      # slightly creative for natural speech
            "maxOutputTokens": 256,  # dubbing lines are short
        },
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        resp = await client.post(_gemini_url(), json=payload, headers=headers)

    if resp.status_code == 429:
        error_body = resp.text[:500]
        logger.warning(f"Gemini 429 response: {error_body}")
        raise RuntimeError(f"Gemini API error 429: {error_body}")

    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    try:
        result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Clean up any quotes Gemini might add
        result = result.strip('"\'')
        return result
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response: {data}")


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


async def _translate_batch_with_gemini(
    texts: list[str],
    source_lang: str,
    target_lang: str,
    segments_context: list = None,
) -> list[str]:
    """
    Translate ALL lines in a single Gemini API call.

    Instead of 246 calls (one per line), this makes 1 call with all lines.
    Gemini's large context window handles 500+ lines easily in one request.
    This completely avoids RPM limits — you use 1 request instead of N requests.

    Returns list of translated strings in the same order as input.
    """
    if not texts:
        return []

    src_name = LANG_NAMES.get(source_lang, source_lang)
    tgt_name = LANG_NAMES.get(target_lang, target_lang)

    # Build numbered list of all lines to translate, each tagged with its
    # speaker. This is the actual context mechanism now: instead of a static
    # preview of only the first few lines, EVERY line carries who said it, for
    # the whole chunk — Gemini can track who's-speaking-to-whom throughout,
    # which matters a lot for short/fragmented lines (a single word like "行"
    # or "我" is close to unreadable in isolation, but is clear once you know
    # which character said it and that the previous line was the same speaker
    # continuing a thought vs. a different character responding).
    numbered_lines = "\n".join(
        f"{i+1}. [{segments_context[i].get('speaker_label', 'SPEAKER')}] {_clean_chinese(text)}"
        if segments_context and i < len(segments_context)
        else f"{i+1}. {_clean_chinese(text)}"
        for i, text in enumerate(texts)
        if text.strip()
    )

    prompt = f"""You are a professional {tgt_name} dubbing scriptwriter with expertise in {src_name} drama/film.

STEP 1 — ANALYZE THE SCENE (do this internally, don't write it out):
Read all the lines below, in order. Each line is tagged with [SPEAKER_XX] —
use these tags to follow the conversation:
- Consecutive lines with the SAME speaker tag are one character continuing a
  thought — translate them so they flow together naturally.
- A line with a DIFFERENT speaker tag than the one before it means a
  different character is now responding — adjust pronouns/address terms to
  fit who is speaking to whom (e.g. a subordinate addressing a superior needs
  different words than two peers talking).
- For EACH distinct speaker tag, infer that character's likely GENDER from
  any clue across the whole set of lines: their name, how other speakers
  address or refer to them, self-references, or context (e.g. "girlfriend",
  "老公"/"husband"). Once you decide a speaker's gender, use it CONSISTENTLY
  for every line from that same tag — never switch mid-conversation. If a
  speaker's gender truly cannot be determined from anything in the lines,
  prefer using their name (if one is known) over guessing a gendered term.
From the full set of lines, determine:
- Era: Is this ancient/wuxia/xianxia (immortals, cultivation, dynasty) or modern/contemporary?
- Tone: Formal/royal, family drama, action, comedy, romantic?
- Relationships: Who is speaking to whom — child to parent, student to master, subjects to royalty?

STEP 2 — CHOOSE THE RIGHT {tgt_name.upper()} REGISTER based on what you detected:

For ANCIENT/WUXIA/XIANXIA (immortals, cultivation, martial arts, historical):
- Use classical Khmer vocabulary that feels like old stories/legends
- Address words: father → "ឪពុក" or "ប្រុស", master → "លោកគ្រូ", elder → "អ្នកតា"
- Speech sounds like Khmer folk tales — dignified but not stiff

For MODERN/CONTEMPORARY (cities, phones, offices, schools):
- Use everyday casual {tgt_name}
- Address words: dad → "ប៉ា", mom → "មា", grandpa → "តា"
- Speech sounds like how young Khmers actually talk today

For BOTH registers:
- Gendered pronouns/address terms MUST match each speaker's inferred gender
  (from STEP 1): refer to or address a MALE character as "លោក", a FEMALE
  character as "អ្នកនាង" or "នាង" — never use the female form for a male
  speaker or vice versa
- SHORT sentences — voice actors need to breathe and sync to video
- Match the EMOTION of each line (anger, sadness, wonder, warmth)
- Keep roughly the SAME LENGTH as the original
- NEVER add words that aren't in the original meaning
- Modern Chinese internet/youth slang (e.g. self-deprecating terms like
  "考研狗", industry jargon like "红圈所") should be adapted to what it
  ACTUALLY MEANS in context, not translated character-by-character — if
  unsure of an exact idiom, prioritize the emotional intent over literal words

STEP 3 — TRANSLATE ALL {len(texts)} LINES from {src_name} to {tgt_name}.

Return ONLY a numbered list, WITHOUT the [SPEAKER_XX] tags — those are for
your reference only, not part of the line:
1. [{tgt_name} spoken line]
2. [{tgt_name} spoken line]
...for all {len(texts)} lines.

No era analysis, no explanations, no speaker tags in the output — just the
numbered {tgt_name} lines.

LINES TO TRANSLATE:
{numbered_lines}

{tgt_name} translations:"""

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": settings.GEMINI_API_KEY,
    }

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 8192,   # enough for 500+ lines
        },
    }

    # Single API call for all lines
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        resp = await client.post(_gemini_url(), json=payload, headers=headers)

    if resp.status_code == 429:
        error_body = resp.text[:500]
        logger.warning(f"Gemini 429 response: {error_body}")
        raise RuntimeError(f"Gemini API error 429: {error_body}")

    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        raise RuntimeError(f"Unexpected Gemini response: {data}")

    # Parse numbered response back into list
    results = [""] * len(texts)
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Match "1. translation" or "1) translation"
        import re
        match = re.match(r'^(\d+)[.)]\s*(.+)$', line)
        if match:
            idx = int(match.group(1)) - 1
            translation = match.group(2).strip().strip('"\'')
            if 0 <= idx < len(results):
                results[idx] = translation

    # Fill any missed lines with deep-translator fallback
    for i, (result, original) in enumerate(zip(results, texts)):
        if not result and original.strip():
            logger.warning(f"Line {i+1} missing from batch response — using deep-translator")
            results[i] = _translate_with_deep(original, source_lang, target_lang)

    return results


async def translate_batch(
    texts: list[str],
    source_lang: str = "zh",
    target_lang: str = "km",
    segments_context: list = None,
) -> list[str]:
    """
    Translate a batch of texts using a single Gemini API call.

    Uses batch processing — all lines sent in one request.
    This uses only 1 RPM credit regardless of how many lines.
    For very large batches (>200 lines), splits into chunks of 150.
    """
    if not texts:
        return []

    backend = settings.TRANSLATION_BACKEND.lower()

    if backend == "gemini" and settings.GEMINI_API_KEY:
        # Split large batches into chunks of 150 lines
        # Each chunk = 1 API call, so 500 lines = 4 calls total
        CHUNK_SIZE = 150
        all_results = []

        chunks = [texts[i:i+CHUNK_SIZE] for i in range(0, len(texts), CHUNK_SIZE)]
        context_chunks = []
        if segments_context:
            context_chunks = [
                segments_context[i:i+CHUNK_SIZE]
                for i in range(0, len(segments_context), CHUNK_SIZE)
            ]

        for chunk_idx, chunk in enumerate(chunks):
            ctx = context_chunks[chunk_idx] if context_chunks else None
            logger.info(
                f"Translating batch {chunk_idx+1}/{len(chunks)} "
                f"({len(chunk)} lines → {target_lang}) — 1 API call"
            )
            try:
                chunk_results = await _translate_batch_with_gemini(
                    chunk, source_lang, target_lang, ctx
                )
                all_results.extend(chunk_results)

                # Small delay between chunks to be safe
                if chunk_idx < len(chunks) - 1:
                    await asyncio.sleep(2.0)

            except Exception as e:
                err_str = str(e)
                # A 429 that's actually depleted credits / billing will NEVER
                # succeed on retry — fall back immediately instead of waiting 60s.
                if "429" in err_str and not _is_billing_error(err_str):
                    logger.warning(f"429 (rate limit) on chunk {chunk_idx+1} — waiting 60s then retrying...")
                    await asyncio.sleep(60)
                    try:
                        chunk_results = await _translate_batch_with_gemini(
                            chunk, source_lang, target_lang, ctx
                        )
                        all_results.extend(chunk_results)
                    except Exception:
                        logger.warning(f"Chunk {chunk_idx+1} failed again — using deep-translator")
                        for text in chunk:
                            all_results.append(
                                _translate_with_deep(text, source_lang, target_lang)
                            )
                else:
                    if _is_billing_error(err_str):
                        logger.error(
                            "Gemini credits depleted — falling back to Google Translate. "
                            "Top up at https://ai.studio/projects to restore Gemini quality."
                        )
                    else:
                        logger.error(f"Gemini batch failed: {e} — using deep-translator")
                    for text in chunk:
                        all_results.append(
                            _translate_with_deep(text, source_lang, target_lang)
                        )

        return all_results

    # Fallback: deep-translator
    sem = asyncio.Semaphore(4)

    async def translate_one_deep(text: str) -> str:
        async with sem:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, _translate_with_deep, text, source_lang, target_lang
            )

    return await asyncio.gather(*[translate_one_deep(t) for t in texts])