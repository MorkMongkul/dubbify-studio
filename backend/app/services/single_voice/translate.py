"""Translation for single-voice dub: every line the actors say, as free, natural spoken Khmer for one voice — not word
for word (the owner: "no need to exactly match what the actor said in Chinese, just make it understandable"; casual or
funny is fine). It never sounds awkward (ឆ្គង), keeps the Story notes' names and words, fits each line's seconds, and
avoids man/woman words where Khmer allows (one voice reads everyone). Gemini through translator's own plumbing
(retries, billing errors); lines it leaves out are asked for once more, then go to the free Google Translate fallback.
Only Khmer someone checked teaches (memory)."""
import asyncio
import logging
import re

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import Job, Segment
from app.services import translator
from app.services.translator import TranslationReport

logger = logging.getLogger(__name__)

CHUNK = 60
MEMORY_MAX = 400
_VOICE_KIND = {"km-KH-PisethNeural": "a man's voice", "km-KH-SreymomNeural": "a woman's voice"}
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s*(.*)$")


async def memory(db: AsyncSession, project_id: str, job_id: str) -> list[tuple[str, str]]:
    """The project's other episodes' (Chinese, Khmer) pairs someone checked: edited, approved, or in an episode marked
    finished — a line only voiced doesn't count (fully automatic mode voices every line unread)."""
    checked = or_(Job.finished_at.is_not(None), Segment.is_approved.is_(True),
                  (Segment.khmer_machine.is_not(None)) & (Segment.khmer_text != Segment.khmer_machine))
    rows = (await db.execute(
        select(Segment.source_text, Segment.khmer_text).join(Job, Job.id == Segment.job_id)
        .where(Job.project_id == project_id, Job.id != job_id, checked)
        .order_by(Job.created_at.desc(), Segment.start_time).limit(MEMORY_MAX * 2)
    )).all()
    pairs = [((zh or "").strip(), (km or "").strip()) for zh, km in rows]
    return [(zh, km) for zh, km in pairs if zh and km][:MEMORY_MAX]


def _prompt(texts: list[str], durations: list[float], story: str, examples: list, voice: str) -> str:
    kind = _VOICE_KIND.get(voice, "one voice")
    story_block = f"ABOUT THIS DRAMA — keep these names and words:\n{story}\n\n" if story else ""
    example_block = ("HOW THIS TEAM SAID OTHER LINES OF THIS DRAMA (reuse their names, terms and tone):\n"
                     + "\n".join(f"{zh} → {km}" for zh, km in examples) + "\n\n") if examples else ""
    numbered = "\n".join(f"{i}. ({d:.1f} s) {translator._clean_chinese(t)}"
                         for i, (t, d) in enumerate(zip(texts, durations), 1))
    return f"""You are dubbing a Chinese drama into Khmer for viewers in Cambodia. ONE narrator-like voice ({kind})
reads every character's lines, so this is not a word-for-word translation:

- Say what each line MEANS the way a Khmer person would really say it: natural, spoken, easy to understand. Casual,
  lively or funny wording is welcome when it fits the scene. Never stiff or awkward (ឆ្គង), never a literal
  character-by-character translation.
- Keep each line short enough to be spoken in the seconds shown before it.
- One voice reads everyone: avoid words that mark the speaker as a man or a woman (បាទ, ចាស, gendered pronouns)
  where Khmer allows; when you can't, use the form that fits {kind}.
- Keep the names and words given below exactly.

{story_block}{example_block}Return ONLY a numbered list, one Khmer line for each of the {len(texts)} lines, no
explanations:
1. [Khmer]
2. [Khmer]

LINES (seconds, then the Chinese):
{numbered}

Khmer:"""


def _parse(raw: str, n: int) -> list[str]:
    out = [""] * n
    for line in (raw or "").splitlines():
        m = _NUMBERED.match(line)
        if m and 1 <= int(m.group(1)) <= n:
            out[int(m.group(1)) - 1] = m.group(2).strip().strip("[]").strip()
    return out


async def _ask(texts, durations, story, examples, voice) -> list[str]:
    payload = {"contents": [{"parts": [{"text": _prompt(texts, durations, story, examples, voice)}]}],
               "generationConfig": {"temperature": 0.7, "maxOutputTokens": translator.GEMINI_MAX_OUTPUT_TOKENS}}
    data = await translator._gemini_with_retries(translator._post_translation, payload,
                                                 httpx.Timeout(180.0, connect=15.0))
    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raw = ""
    return _parse(raw, len(texts))


async def translate(texts: list[str], durations: list[float], story: str, memory_pairs: list,
                    voice: str) -> tuple[list[str], TranslationReport]:
    """Each line in Khmer ("" where nothing could translate it) and how it went."""
    report = TranslationReport()
    results = [""] * len(texts)
    use_gemini = settings.TRANSLATION_BACKEND.lower() == "gemini" and bool(settings.GEMINI_API_KEY)
    for start in range(0, len(texts), CHUNK):
        chunk = list(range(start, min(len(texts), start + CHUNK)))
        todo = [i for i in chunk if (texts[i] or "").strip()]
        examples = translator.pick_examples(memory_pairs, [texts[i] for i in todo]) if memory_pairs else []
        for _attempt in range(2 if use_gemini else 0):   # the lines Gemini left out are asked for once more
            if not todo:
                break
            try:
                got = await _ask([texts[i] for i in todo], [durations[i] for i in todo], story, examples, voice)
            except translator.GeminiError as e:
                report.gemini_error = str(e)
                logger.warning(f"Single-voice translation: Gemini failed: {e}")
                break
            for i, km in zip(todo, got):
                results[i] = "" if translator._looks_untranslated(km, "zh") else km   # Chinese echoed back
            todo = [i for i in todo if not results[i]]
        if todo:   # the free fallback: no story, no style, but better than nothing
            # in a thread: it throttles itself with time.sleep, which would stop the whole backend
            backup = await asyncio.to_thread(translator._deep_translate_many, [texts[i] for i in todo], "zh", "km")
            for i, km in zip(todo, backup):
                results[i] = km
                if km:
                    report.fallback += 1
                else:
                    report.failed += 1
    return results, report
