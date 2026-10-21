"""
app/services/translator.py
Translation using Google Gemini API with full conversation context.

Gemini sees the ENTIRE conversation before translating each line —
this produces natural, emotional, dubbing-quality translations instead
of robotic literal ones.

Also fixes the ASR spacing issue:
  "我 回 来了" → "我回来了" before translating

Backends (set TRANSLATION_BACKEND in .env):
  "gemini"  → Gemini with scene context (best quality, recommended)
  "deep"    → Google Translate via deep-translator (fast, no context)

Getting through failures, in order (so a line is left empty only when every
route is down):
  1. The model: a retired Gemini model is replaced automatically
     (app/services/gemini_models.py) — never a reason to fall back.
  2. Temporary errors (rate limits, overload, network) are retried with backoff.
  3. Lines missing from Gemini's answer are asked for again on their own.
  4. Only then the no-context Google Translate fallback — throttled, batched
     and retried, because that free endpoint rejects bursts of requests.
Failed lines stay "" (never the source text), and the report says why.

Get your Gemini API key at: aistudio.google.com
"""
import logging
import asyncio
import re
import threading
import time
from dataclasses import dataclass

import httpx
from app.core.config import settings
from app.services import gemini_models

logger = logging.getLogger(__name__)

# Lines per Gemini request. Khmer output is token-heavy; smaller chunks keep
# the answer well inside the output limit (a cut-off answer drops lines).
CHUNK_SIZE = 60
GEMINI_MAX_OUTPUT_TOKENS = 32768
GEMINI_ATTEMPTS = 3
GEMINI_RETRY_DELAYS = (5.0, 20.0)     # between attempts, unless Google says how long to wait


class GeminiError(RuntimeError):
    """A failed Gemini call; `transient` errors (rate limit, overload, network) are worth retrying."""

    def __init__(self, message: str, *, transient: bool, retry_after: float | None = None):
        super().__init__(message)
        self.transient = transient
        self.retry_after = retry_after

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
    Remove spaces between Chinese characters added by the ASR output.
    "我 回 来了" → "我回来了"
    Preserves spaces between Latin words if mixed content.
    """
    import re
    # Remove spaces between CJK characters
    cleaned = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', text)
    # Remove leading/trailing spaces
    return cleaned.strip()


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Server-suggested wait: the Retry-After header, or Gemini's RetryInfo.retryDelay ("17s")."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return min(60.0, float(header))
        except ValueError:
            pass
    try:
        for detail in resp.json().get("error", {}).get("details", []):
            delay = str(detail.get("retryDelay", ""))
            if delay.endswith("s"):
                return min(60.0, float(delay[:-1]))
    except (ValueError, AttributeError):
        pass
    return None


async def _post_translation(payload: dict, timeout: httpx.Timeout) -> dict:
    """One generateContent call for translation; raises GeminiError on any failure."""
    try:
        resp = await gemini_models.generate_content("translation", payload, timeout=timeout)
    except httpx.HTTPError as e:
        raise GeminiError(f"Gemini request failed ({type(e).__name__})", transient=True) from e
    if resp.status_code == 200:
        return resp.json()
    message = f"Gemini {resp.status_code}: {gemini_models.error_message(resp.text)}"
    if resp.status_code == 429:
        # Depleted credits also come back as 429 — waiting won't fix those.
        raise GeminiError(message, transient=not _is_billing_error(resp.text), retry_after=_retry_after_seconds(resp))
    raise GeminiError(message, transient=resp.status_code >= 500)


async def _gemini_with_retries(fn, *args):
    """Run a Gemini call, retrying temporary failures with backoff."""
    for attempt in range(1, GEMINI_ATTEMPTS + 1):
        try:
            return await fn(*args)
        except GeminiError as e:
            if not e.transient or attempt == GEMINI_ATTEMPTS:
                raise
            delay = e.retry_after if e.retry_after is not None else GEMINI_RETRY_DELAYS[min(attempt, len(GEMINI_RETRY_DELAYS)) - 1]
            logger.warning(f"{e} — retrying in {delay:.0f}s (attempt {attempt}/{GEMINI_ATTEMPTS})")
            await asyncio.sleep(delay)


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _looks_untranslated(text: str, source_lang: str) -> bool:
    """A Chinese → other-language result that still contains Chinese characters wasn't translated."""
    return source_lang == "zh" and bool(_CJK_RE.search(text or ""))


def _translate_with_deep(text: str, source_lang: str, target_lang: str) -> str:
    """
    Fallback: Google Translate via deep-translator. Fast but no context.
    Returns "" when it fails — never the source text, which would otherwise
    be stored as the "translation" and voiced by TTS in the source language.
    """
    try:
        from deep_translator import GoogleTranslator
        clean = _clean_chinese(text)
        src = GOOGLE_LANG_MAP.get(source_lang, source_lang)
        tgt = GOOGLE_LANG_MAP.get(target_lang, target_lang)
        result = (GoogleTranslator(source=src, target=tgt).translate(clean) or "").strip()
    except Exception as e:
        logger.error(f"deep-translator failed: {e}")
        return ""
    if _looks_untranslated(result, source_lang):
        logger.warning("deep-translator returned untranslated text — leaving the line empty")
        return ""
    return result


# ── Google Translate fallback: batched, throttled, retried ─────────────────
# The free endpoint behind deep-translator rejects bursts ("You made too many
# requests") — one request per line, back to back, is exactly what fails.
# Lines are sent together (newline-joined), requests are spaced out, and a
# rejected request is retried after a pause.

DEEP_GROUP_LINES = 20
DEEP_GROUP_CHARS = 3500          # the endpoint's own limit is 5000 characters
DEEP_ATTEMPTS = 3
DEEP_BACKOFF = (3.0, 10.0)       # seconds before the 2nd and 3rd attempt
DEEP_MIN_INTERVAL = 0.6          # seconds between requests

_deep_sleep = time.sleep         # tests replace this
_deep_lock = threading.Lock()
_deep_last_request = 0.0


def _deep_request(text: str, source_lang: str, target_lang: str) -> str | None:
    """One Google Translate request with spacing and retries. None when it kept failing."""
    global _deep_last_request
    from deep_translator import GoogleTranslator
    src = GOOGLE_LANG_MAP.get(source_lang, source_lang)
    tgt = GOOGLE_LANG_MAP.get(target_lang, target_lang)
    for attempt in range(1, DEEP_ATTEMPTS + 1):
        with _deep_lock:
            wait = DEEP_MIN_INTERVAL - (time.monotonic() - _deep_last_request)
            if wait > 0:
                _deep_sleep(wait)
            _deep_last_request = time.monotonic()
        try:
            return (GoogleTranslator(source=src, target=tgt).translate(text) or "").strip()
        except Exception as e:
            if attempt == DEEP_ATTEMPTS:
                logger.error(f"deep-translator failed after {attempt} attempts: {str(e)[:200]}")
                return None
            delay = DEEP_BACKOFF[attempt - 1]
            logger.warning(f"deep-translator: {str(e)[:120]} — retrying in {delay:.0f}s")
            _deep_sleep(delay)
    return None


def _deep_translate_many(lines: list[str], source_lang: str, target_lang: str) -> list[str]:
    """
    Translate many lines with as few requests as possible. A batch whose answer
    doesn't split back into the same number of lines is redone line by line.
    Failed or untranslated lines come back "".
    """
    results = [""] * len(lines)
    todo = [(i, _clean_chinese(text)) for i, text in enumerate(lines) if text and text.strip()]

    def accept(i: int, text: str | None) -> None:
        if text and not _looks_untranslated(text, source_lang):
            results[i] = text.strip()

    group: list[tuple[int, str]] = []

    def flush() -> None:
        if not group:
            return
        if len(group) == 1:
            i, text = group[0]
            accept(i, _deep_request(text, source_lang, target_lang))
        else:
            answer = _deep_request("\n".join(t for _, t in group), source_lang, target_lang)
            parts = [p for p in (answer or "").split("\n")]
            if answer and len(parts) == len(group):
                for (i, _), part in zip(group, parts):
                    accept(i, part)
            else:
                for i, text in group:
                    accept(i, _deep_request(text, source_lang, target_lang))
        group.clear()

    for i, text in todo:
        if "\n" in text:
            flush()
            group.append((i, text.replace("\n", " ")))
            flush()
            continue
        if len(group) >= DEEP_GROUP_LINES or sum(len(t) + 1 for _, t in group) + len(text) > DEEP_GROUP_CHARS:
            flush()
        group.append((i, text))
    flush()
    return results


# ── Main entry points ──────────────────────────────────────────

async def translate_text(
    text: str,
    source_lang: str = "zh",
    target_lang: str = "km",
) -> str:
    """Translate a single text string (no context)."""
    if not text.strip():
        return ""
    results, _report = await translate_batch_with_report([text], source_lang, target_lang)
    return results[0]


async def _translate_batch_with_gemini(
    texts: list[str],
    source_lang: str,
    target_lang: str,
    segments_context: list = None,
    story: str = "",
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

    # The project's story notes (story_notes.for_translation): names, genders and
    # forms of address from the user and from finished episodes, so they stay
    # the same from one episode to the next instead of being guessed afresh.
    story_block = f"{story}\n\n" if story else ""
    story_rule = (
        "\n- If ABOUT THIS DRAMA gives a character's gender or how they address\n"
        "  someone, use it instead of guessing." if story else ""
    )

    prompt = f"""You are a professional {tgt_name} dubbing scriptwriter with expertise in {src_name} drama/film.

{story_block}STEP 1 — ANALYZE THE SCENE (do this internally, don't write it out):
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
  prefer using their name (if one is known) over guessing a gendered term.{story_rule}
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

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS,
        },
    }

    data = await _post_translation(payload, httpx.Timeout(180.0, connect=15.0))
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        raise GeminiError(
            f"Gemini returned no translation{f' (blocked: {blocked})' if blocked else ''}",
            transient=not blocked,
        )

    # Parse numbered response back into list
    results = [""] * len(texts)
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Match "1. translation" or "1) translation"
        match = re.match(r'^(\d+)[.)]\s*(.+)$', line)
        if match:
            idx = int(match.group(1)) - 1
            translation = match.group(2).strip().strip('"\'')
            if 0 <= idx < len(results):
                results[idx] = translation

    # Lines the model skipped stay "" here — translate_batch_with_report fills
    # them with the fallback and counts them, so the job can say so.
    return results


@dataclass
class TranslationReport:
    """How a batch was translated: lines the no-context fallback had to do, lines left empty, and why."""
    fallback: int = 0
    failed: int = 0
    gemini_error: str = ""

    def warning(self) -> str:
        parts = []
        if self.failed:
            parts.append(
                f"{self.failed} line(s) couldn't be translated and were left empty — "
                "use Translate missing lines in the transcript to try again."
            )
        if self.fallback:
            parts.append(
                f"{self.fallback} line(s) were translated by the basic fallback (no scene "
                "context) because Gemini was unavailable — review them before generating voices."
            )
        if parts and self.gemini_error:
            parts.append(f"Gemini said: {self.gemini_error}")
        return " ".join(parts)


def _needs_translation(source: str, result: str, source_lang: str) -> bool:
    return bool(source.strip()) and (not result or _looks_untranslated(result, source_lang))


async def _finish_lines(
    texts: list[str], results: list[str], source_lang: str, target_lang: str,
    report: TranslationReport, count_fallback: bool,
) -> list[str]:
    """Send every line still missing a translation to the fallback (in batches), and count the outcome."""
    finished = list(results)
    missing = [i for i, (src, res) in enumerate(zip(texts, results)) if _needs_translation(src, res, source_lang)]
    for i, (src, res) in enumerate(zip(texts, results)):
        if not src.strip():
            finished[i] = ""
    if not missing:
        return finished
    # deep-translator is blocking `requests` — keep it off the event loop
    fixed = await asyncio.to_thread(_deep_translate_many, [texts[i] for i in missing], source_lang, target_lang)
    for i, text in zip(missing, fixed):
        finished[i] = text
        if text:
            report.fallback += int(count_fallback)
        else:
            report.failed += 1
    return finished


async def _translate_chunk(
    chunk: list[str], source_lang: str, target_lang: str, ctx: list | None, report: TranslationReport,
    story: str = "",
) -> list[str]:
    """Gemini for one chunk: retried call, then one more request for any lines its answer left out."""
    try:
        results = await _gemini_with_retries(_translate_batch_with_gemini, chunk, source_lang, target_lang, ctx, story)
    except Exception as e:
        report.gemini_error = report.gemini_error or str(e)[:300]
        logger.error(f"Gemini translation failed: {e} — these lines go to the fallback")
        return [""] * len(chunk)
    results = list(results) + [""] * (len(chunk) - len(results))

    missing = [i for i, (src, res) in enumerate(zip(chunk, results)) if _needs_translation(src, res, source_lang)]
    if missing:
        logger.warning(f"{len(missing)} line(s) missing from Gemini's answer — asking again for just those")
        try:
            again = await _gemini_with_retries(
                _translate_batch_with_gemini,
                [chunk[i] for i in missing], source_lang, target_lang,
                [ctx[i] for i in missing] if ctx else None,
                story,
            )
            for i, res in zip(missing, again):
                if not _needs_translation(chunk[i], res, source_lang):
                    results[i] = res
        except Exception as e:
            report.gemini_error = report.gemini_error or str(e)[:300]
            logger.error(f"Gemini retry for missing lines failed: {e}")
    return results


async def translate_batch_with_report(
    texts: list[str],
    source_lang: str = "zh",
    target_lang: str = "km",
    segments_context: list = None,
    story: str = "",
) -> tuple[list[str], TranslationReport]:
    """
    Translate a batch (one Gemini call per chunk of CHUNK_SIZE lines) and report
    how it went. See the module docstring for the order of fallbacks; lines
    nothing could translate are left "" — never the source text. `story` is
    the project's story notes as story_notes.for_translation gives them.
    """
    report = TranslationReport()
    if not texts:
        return [], report

    backend = settings.TRANSLATION_BACKEND.lower()

    if backend == "gemini" and settings.GEMINI_API_KEY:
        results: list[str] = []
        for start in range(0, len(texts), CHUNK_SIZE):
            chunk = texts[start:start + CHUNK_SIZE]
            ctx = segments_context[start:start + CHUNK_SIZE] if segments_context else None
            logger.info(
                f"Translating lines {start + 1}-{start + len(chunk)} of {len(texts)} → {target_lang}"
            )
            results.extend(await _translate_chunk(chunk, source_lang, target_lang, ctx, report, story))
        return await _finish_lines(texts, results, source_lang, target_lang, report, count_fallback=True), report

    # deep-translator as the configured backend (or Gemini configured without a key)
    results = await _finish_lines(texts, [""] * len(texts), source_lang, target_lang, report, count_fallback=False)
    if backend == "gemini":
        report.fallback = sum(1 for r in results if r)   # Gemini wanted, but no key configured
    return results, report


async def translate_batch(
    texts: list[str],
    source_lang: str = "zh",
    target_lang: str = "km",
    segments_context: list = None,
) -> list[str]:
    """Translate a batch; see translate_batch_with_report for the details and the counts."""
    results, _report = await translate_batch_with_report(texts, source_lang, target_lang, segments_context)
    return results
