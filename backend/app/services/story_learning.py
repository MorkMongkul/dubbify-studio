"""
app/services/story_learning.py
Learning a project's story notes from the episodes the user marked finished.

Each finished episode the notes haven't learned yet is read — its approved
lines, in order, with who says them — and Gemini (the Assistant's model) is
asked what they tell about the characters (Khmer names, gender, how they
address each other), the words to keep, the story and the tone. The answer is
merged with story_notes.merge_learned, which never touches what the user
wrote or corrected.

Runs as a background task, one run per project, LEARN_BATCH episodes per
request: the first run typically reads episodes 1–5 together, later ones the
episode just finished. The run's state lives in memory (learning_state), so
the Story panel can show it; a restart ends a run, and the episodes it hadn't
reached are read by the next one. No database session stays open during a
Gemini call, and each answer is merged into the notes as they are by then
(the project row locked), so an edit the user made meanwhile survives.
"""
import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core import database as core_db
from app.models.models import Job, Project, Segment, Speaker
from app.services import gemini_models, story_notes
from app.services.pipeline_common import episodes as project_episodes
from app.services.translator import GeminiError, _gemini_with_retries

logger = logging.getLogger(__name__)

LEARN_BATCH = 5

_runs: dict[str, dict] = {}   # project id → {"state", "message"}; absent = idle

_INSTRUCTION = """You keep the story notes of a Chinese drama being dubbed into Khmer. You get the notes so far and the dialogue of episodes whose Khmer the user has checked: treat that Khmer as correct.

From these episodes, return:
- characters: every named character who speaks or is spoken about. zh = the name as written in the Chinese. km = the Khmer name used in the checked lines ("" if it never appears). about = one short line in English: man or woman, rough age, role, how they address others and how others address them (quote the Khmer forms actually used, e.g. បង, អូន, លោក), and how they speak. speaker = their [SPEAKER_XX] tag when their lines carry one, else "".
- words: recurring terms, titles, places and things whose Khmer must stay the same. zh, km as used, and an optional short note.
- summaries: for each episode given, 1 to 3 sentences in English on what happens, with its episode number.
- tone: one or two sentences on the drama's register and style.

Base everything on the checked lines and the notes; never invent. Items marked (you) were written by the user: never contradict them. Keep every line short."""

_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "tone": {"type": "STRING"},
        "characters": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "zh": {"type": "STRING"}, "km": {"type": "STRING"},
            "about": {"type": "STRING"}, "speaker": {"type": "STRING"},
        }, "required": ["zh", "km", "about"]}},
        "words": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "zh": {"type": "STRING"}, "km": {"type": "STRING"}, "note": {"type": "STRING"},
        }, "required": ["zh", "km"]}},
        "summaries": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "episode": {"type": "INTEGER"}, "summary": {"type": "STRING"},
        }, "required": ["episode", "summary"]}},
    },
    "required": ["characters", "words", "summaries"],
}


def learning_state(project_id: str) -> dict:
    run = _runs.get(project_id)
    return dict(run) if run else {"state": "idle", "message": ""}


def claim(project_id: str) -> bool:
    """Start a run for this project unless one is already running."""
    if _runs.get(project_id, {}).get("state") == "running":
        return False
    _runs[project_id] = {"state": "running", "message": "Getting ready…"}
    return True


def _episodes_label(numbers: list[int]) -> str:
    if len(numbers) == 1:
        return f"episode {numbers[0]}"
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"episodes {numbers[0]}–{numbers[-1]}"
    return "episodes " + ", ".join(map(str, numbers[:-1])) + f" and {numbers[-1]}"


def _line(label: str | None, name: str | None, zh: str | None, km: str | None) -> str:
    who = label or "?"
    if name and name != label:
        who += f" · {name}"
    said = (zh or "").strip()
    if (km or "").strip():
        said += f" → {km.strip()}" if said else km.strip()
    return f"[{who}] {said}"


async def _next_batch(project_id: str) -> tuple[str, list[dict]]:
    """The notes as the prompt shows them, and the next finished episodes not learned yet, with their lines."""
    async with core_db.AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if project is None:
            return "", []
        notes = story_notes.normalize(project.story_notes)
        jobs = await project_episodes(db, project_id)
        todo = [(n, job) for n, job in enumerate(jobs, start=1)
                if job.finished_at is not None and job.id not in notes["learned"]][:LEARN_BATCH]
        batch = []
        for number, job in todo:
            rows = await db.execute(
                select(Speaker.label, Speaker.display_name, Segment.source_text, Segment.khmer_text)
                .select_from(Segment).outerjoin(Speaker, Segment.speaker_id == Speaker.id)
                .where(Segment.job_id == job.id).order_by(Segment.start_time))
            lines = [_line(*row) for row in rows.all() if (row[2] or "").strip() or (row[3] or "").strip()]
            batch.append({"number": number, "job_id": job.id, "title": job.title or "", "lines": lines})
        return story_notes.for_assistant(notes, [job.id for job in jobs], marks=True), batch


def _payload(notes_text: str, batch: list[dict]) -> dict:
    episodes = "\n\n".join(
        f"=== Episode {e['number']} ===" + (f" ({e['title']})" if e["title"] else "") + "\n" + "\n".join(e["lines"])
        for e in batch)
    text = (notes_text or "No notes yet.") + "\n\nEPISODES TO LEARN FROM:\n\n" + episodes
    return {
        "systemInstruction": {"parts": [{"text": _INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }


async def _ask_gemini(payload: dict) -> dict:
    """One learning request; the parsed JSON answer. Raises on failure (busy or overloaded: retried first)."""
    async def call() -> dict:
        try:
            resp = await gemini_models.generate_content("chat", payload, timeout=httpx.Timeout(180.0, connect=10.0))
        except httpx.HTTPError as e:
            raise GeminiError(f"Gemini request failed ({type(e).__name__})", transient=True) from e
        if resp.status_code != 200:
            raise GeminiError(f"Gemini {resp.status_code}: {gemini_models.error_message(resp.text)}",
                              transient=resp.status_code == 429 or resp.status_code >= 500)
        candidate = (resp.json().get("candidates") or [{}])[0]
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise GeminiError("Gemini's answer was cut off", transient=False)
        text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
        try:
            return json.loads(text)
        except ValueError as e:
            raise GeminiError("Gemini's answer wasn't readable", transient=False) from e

    return await _gemini_with_retries(call)


def _learned(answer, jobs_by_number: dict[int, str]) -> dict:
    """The answer in merge_learned's shape; summaries for episodes outside this batch are dropped."""
    answer = answer if isinstance(answer, dict) else {}
    summaries = {}
    for item in answer.get("summaries") or []:
        if isinstance(item, dict) and item.get("episode") in jobs_by_number:
            summaries[jobs_by_number[item["episode"]]] = item.get("summary") or ""
    return {"tone": answer.get("tone") or "", "characters": answer.get("characters") or [],
            "words": answer.get("words") or [], "summaries": summaries}


async def _merge(project_id: str, learned: dict, job_ids: list[str]) -> None:
    async with core_db.AsyncSessionLocal() as db:
        result = await db.execute(select(Project).where(Project.id == project_id).with_for_update())
        project = result.scalar_one_or_none()
        if project is None:
            return
        project.story_notes = story_notes.merge_learned(project.story_notes, learned, job_ids)
        flag_modified(project, "story_notes")
        await db.commit()


async def learn_from_finished(project_id: str) -> None:
    """Read every finished episode the notes haven't learned yet, a batch at a time (background task)."""
    numbers: list[int] = []
    try:
        while True:
            notes_text, batch = await _next_batch(project_id)
            if not batch:
                break
            numbers = [e["number"] for e in batch]
            _runs[project_id] = {"state": "running", "message": f"Reading {_episodes_label(numbers)}…"}
            spoken = [e for e in batch if e["lines"]]
            learned = {}
            if spoken:      # an episode without lines has nothing to teach, but counts as read
                answer = await _ask_gemini(_payload(notes_text, spoken))
                learned = _learned(answer, {e["number"]: e["job_id"] for e in spoken})
            await _merge(project_id, learned, [e["job_id"] for e in batch])
        _runs.pop(project_id, None)
    except Exception as e:
        logger.warning(f"Story notes for project {project_id[:8]}: learning stopped — {e}")
        where = f" {_episodes_label(numbers)}" if numbers else " the episodes"
        _runs[project_id] = {"state": "failed", "message": f"Couldn't read{where}: {str(e)[:200]}"}
