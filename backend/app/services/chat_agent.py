"""
app/services/chat_agent.py
Assistant chat — a project-scoped agent that can search a project's dialogue
segments across every episode and propose/apply translation edits, using
Gemini function calling (generateContent + functionDeclarations/functionCall/
functionResponse — the classic REST pattern, not the newer Interactions API;
see the note below for why).

Conversation history is stored locally in ChatMessage rows and replayed into
a fresh `contents` array on every turn, rather than relying on Gemini's own
server-side interaction state (the newer /v1beta/interactions endpoint). Two
reasons: (1) the UI needs a durable local transcript regardless — the editor
already persists every other kind of state in its own DB, and a chat history
tied only to Google's retention policy would be the one exception; (2) the
classic generateContent + functionDeclarations pattern is long-proven and
already used elsewhere in this codebase (translator.py, tts_client.py),
where the newer Interactions endpoint is not.

Tools:
  search_segments  — find segments by text/speaker/episode across the project
  list_episodes    — list the project's episodes (jobs) with basic info
  propose_edit     — change a segment's Khmer translation; auto-applies or
                     stages for approval depending on Project.chat_edit_mode
"""
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, func, or_, literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import gemini_models
from app.services.pipeline_common import episode_ids
from app.models.models import ChatMessage, ChatProposedEdit, Job, Project, Segment, Speaker

logger = logging.getLogger(__name__)


def naive_utcnow() -> datetime:
    """The DateTime columns here are TIMESTAMP WITHOUT TIME ZONE (matching
    every other timestamp column in this codebase, which all rely on the
    DB-side func.now() rather than a Python-side value) — asyncpg rejects a
    tz-aware datetime.now(timezone.utc) against that column type outright.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)



_SYSTEM_INSTRUCTION = """You are a translation-consistency assistant for a Chinese-to-Khmer \
movie/drama dubbing project. Your job is to help the user keep character \
names, terms, and tone consistent across episodes, and to explain or fix \
translation lines when asked.

Ground yourself in the project's ACTUAL prior episodes before answering — \
use search_segments to look up how something was translated or referred to \
before, rather than guessing. Use list_episodes if you need to know how many \
episodes exist or their status.

When the user asks you to fix or change a line, use propose_edit. Always \
give a clear, specific reason — even in auto-apply mode, the user needs to \
understand why you changed it. Keep replacements roughly the same length as \
what they replace (voice actors sync TTS timing to the original line), and \
write them as natural spoken dialogue, not literal/formal translation — \
this project's existing batch translator already follows that style, match it.

Keep your own chat replies concise — a sentence or two of explanation, not \
an essay, unless the user asks for detail."""


_TOOL_DECLARATIONS = [
    {
        "name": "search_segments",
        "description": (
            "Search this project's dialogue segments across all episodes. Use "
            "this to check how a character, phrase, or term was translated "
            "before, or to find the segment(s) the user is asking about."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Substring to search for in the original (Chinese) or translated (Khmer) text, case-insensitive. Omit to filter only by speaker/episode.",
                },
                "speaker_label": {
                    "type": "STRING",
                    "description": "Filter to one speaker's lines, e.g. \"SPEAKER_00\". Learn valid labels from a prior search result.",
                },
                "episode_number": {
                    "type": "INTEGER",
                    "description": "Filter to one episode, numbered from 1 in upload order. Use list_episodes to see how many exist.",
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Max segments to return. Default 30, max 100.",
                },
            },
        },
    },
    {
        "name": "list_episodes",
        "description": "List every episode (job) in this project with its episode number, status, and segment count.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "propose_edit",
        "description": (
            "Change a segment's Khmer translation. Depending on the project's "
            "approval mode this either applies immediately or is staged for "
            "the user to approve in the editor — always explain your reasoning."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "segment_id": {"type": "STRING", "description": "The segment to edit (from a search_segments result)."},
                "new_khmer_text": {"type": "STRING", "description": "The replacement Khmer line."},
                "reason": {"type": "STRING", "description": "Why this is a better translation than what's there now."},
            },
            "required": ["segment_id", "new_khmer_text", "reason"],
        },
    },
]


# ── Tool implementations ────────────────────────────────────────

async def _tool_search_segments(db: AsyncSession, project_id: str, args: dict) -> dict:
    query = (args.get("query") or "").strip()
    speaker_label = (args.get("speaker_label") or "").strip()
    episode_number = args.get("episode_number")
    limit = min(100, max(1, int(args.get("limit") or 30)))

    job_ids = await episode_ids(db, project_id)
    job_id_filter = None
    if episode_number:
        if not (1 <= episode_number <= len(job_ids)):
            return {"error": f"No episode #{episode_number} — this project has {len(job_ids)} episode(s)."}
        job_id_filter = job_ids[episode_number - 1]

    stmt = (
        select(Segment, Job.id, Speaker.label)
        .join(Job, Segment.job_id == Job.id)
        .outerjoin(Speaker, Segment.speaker_id == Speaker.id)
        .where(Job.project_id == project_id)
    )
    if job_id_filter:
        stmt = stmt.where(Segment.job_id == job_id_filter)
    if speaker_label:
        stmt = stmt.where(Speaker.label == speaker_label)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(or_(Segment.source_text.ilike(like), Segment.khmer_text.ilike(like)))
    stmt = stmt.order_by(Job.created_at, Segment.start_time).limit(limit)

    rows = (await db.execute(stmt)).all()
    order = {jid: i + 1 for i, jid in enumerate(job_ids)}

    return {
        "count": len(rows),
        "segments": [
            {
                "segment_id": seg.id,
                "episode_number": order.get(seg_job_id),
                "speaker_label": spk_label or "",
                "start_time": seg.start_time,
                "source_text": seg.source_text,
                "khmer_text": seg.khmer_text,
                "is_approved": seg.is_approved,
            }
            for seg, seg_job_id, spk_label in rows
        ],
    }


async def _tool_list_episodes(db: AsyncSession, project_id: str, args: dict) -> dict:
    result = await db.execute(select(Job).where(Job.project_id == project_id).order_by(Job.created_at))
    episodes = []
    for i, job in enumerate(result.scalars().all(), start=1):
        count = (await db.execute(
            select(func.count(Segment.id)).where(Segment.job_id == job.id)
        )).scalar() or 0
        episodes.append({
            "episode_number": i,
            "title": job.title or "",
            "job_id": job.id,
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "segment_count": count,
            "duration_secs": job.duration_secs,
        })
    return {"episodes": episodes}


async def _tool_propose_edit(db: AsyncSession, project: Project, args: dict, message_id: str | None) -> dict:
    segment_id = (args.get("segment_id") or "").strip()
    new_text = (args.get("new_khmer_text") or "").strip()
    reason = (args.get("reason") or "").strip()
    if not segment_id or not new_text:
        return {"error": "segment_id and new_khmer_text are required"}

    seg = (await db.execute(select(Segment).where(Segment.id == segment_id))).scalar_one_or_none()
    if not seg:
        return {"error": f"No segment with id {segment_id}"}

    # A tool-call argument is model-generated input — confirm the segment
    # actually belongs to THIS project rather than trusting it blindly.
    seg_project_id = (await db.execute(select(Job.project_id).where(Job.id == seg.job_id))).scalar_one_or_none()
    if seg_project_id != project.id:
        return {"error": "That segment does not belong to this project."}

    auto = project.chat_edit_mode == "auto"
    edit = ChatProposedEdit(
        project_id=project.id,
        segment_id=segment_id,
        message_id=message_id,
        old_khmer_text=seg.khmer_text,
        new_khmer_text=new_text,
        reason=reason,
        status="approved" if auto else "pending",
        resolved_at=naive_utcnow() if auto else None,
    )
    db.add(edit)

    if auto:
        apply_edit_to_segment(seg, new_text)
        await db.flush()
        return {"status": "applied", "edit_id": edit.id}

    await db.flush()
    return {
        "status": "pending_approval",
        "edit_id": edit.id,
        "note": "Staged — waiting for the user to approve this in the Assistant panel. Do not treat it as done.",
    }


def apply_edit_to_segment(seg: Segment, new_khmer_text: str) -> None:
    """Shared by auto-apply and the manual approve endpoint (routes/chat.py).

    Clears tts_audio_path — same "stale clip after a text edit" convention
    SegmentUpdate uses — and un-approves the segment: a changed translation,
    even one the user trusted the AI to apply automatically, needs a human
    look before it's synthesized again.
    """
    seg.khmer_text = new_khmer_text
    seg.tts_audio_path = ""
    seg.is_approved = False


_TOOL_IMPLS = {
    "search_segments": _tool_search_segments,
    "list_episodes": _tool_list_episodes,
}


# ── Gemini call + agent loop ────────────────────────────────────

async def _call_gemini(contents: list[dict]) -> dict:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set — the Assistant needs it to run.")

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": _SYSTEM_INSTRUCTION}]},
        "tools": [{"functionDeclarations": _TOOL_DECLARATIONS}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
    }
    # A retired chat model is replaced automatically (gemini_models).
    resp = await gemini_models.generate_content("chat", payload, timeout=httpx.Timeout(60.0, connect=10.0))
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {gemini_models.error_message(resp.text)}")

    data = resp.json()
    try:
        return data["candidates"][0]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Unexpected Gemini response: {data}")


def _messages_to_contents(messages: list[ChatMessage]) -> list[dict]:
    contents = []
    for m in messages:
        if m.function_call:
            # raw_parts is Gemini's own response parts, replayed verbatim —
            # see the note in run_chat_turn on why this can't be a clean
            # hand-reconstructed {"functionCall": {...}}.
            parts = m.function_call.get("raw_parts") or [
                {"functionCall": {"name": m.function_call.get("name"), "args": m.function_call.get("args")}}
            ]
        elif m.function_response:
            parts = [{"functionResponse": m.function_response}]
        else:
            parts = [{"text": m.text}]
        contents.append({"role": m.role, "parts": parts})
    return contents


def message_order(newest_first: bool = False) -> list:
    """
    Conversation order. Messages saved by older versions share whole-second
    timestamps; on SQLite the row id breaks those ties in the order they were
    saved.
    """
    keys = [ChatMessage.created_at]
    if "sqlite" in settings.effective_db_url:
        keys.append(literal_column("chat_messages.rowid"))
    return [k.desc() for k in keys] if newest_first else keys


async def run_chat_turn(db: AsyncSession, project: Project, user_text: str) -> list[ChatMessage]:
    """Run one full turn: persist the user's message, loop Gemini calls +
    tool execution until it gives a plain text reply (or the round cap is
    hit), and return every ChatMessage row this turn created — in order —
    so the caller can hand them straight to the frontend without a re-query.

    Commits right before every Gemini call (never holds the connection
    across one) — SQLite's pool_size=1 means a multi-round tool-calling turn
    that kept the connection open the whole time would block every other
    request (job polls, segment fetches) for however long Gemini takes.
    """
    history_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.project_id == project.id)
        .order_by(*message_order(newest_first=True))
        .limit(settings.CHAT_HISTORY_LIMIT)
    )
    history = list(reversed(history_result.scalars().all()))

    user_msg = ChatMessage(project_id=project.id, role="user", text=user_text)
    db.add(user_msg)
    await db.commit()

    new_messages: list[ChatMessage] = [user_msg]
    contents = _messages_to_contents(history) + _messages_to_contents([user_msg])

    for _round in range(max(1, settings.CHAT_MAX_TOOL_ROUNDS)):
        model_content = await _call_gemini(contents)
        parts = model_content.get("parts", [])
        function_call_part = next((p for p in parts if "functionCall" in p), None)
        text = "".join(p.get("text", "") for p in parts if "text" in p)

        if not function_call_part:
            model_msg = ChatMessage(project_id=project.id, role="model", text=text or "(no response)")
            db.add(model_msg)
            await db.commit()
            new_messages.append(model_msg)
            return new_messages

        function_call = function_call_part["functionCall"]
        # Store Gemini's raw response parts (raw_parts), not just a hand-picked
        # {name, args} — "thinking" models attach extra fields to function-call
        # parts (e.g. a thoughtSignature) that MUST be echoed back unchanged on
        # the next call or the API rejects it (confirmed live: gemini-3.6-flash
        # 400s with "Function call is missing a thought_signature" if you
        # reconstruct a clean part instead of replaying the original). Treating
        # the response as an opaque blob to replay is more robust than trying
        # to track every such undocumented field by name.
        stored_function_call = {
            "name": function_call.get("name", ""),
            "args": function_call.get("args", {}) or {},
            "raw_parts": parts,
        }
        model_msg = ChatMessage(project_id=project.id, role="model", text=text, function_call=stored_function_call)
        db.add(model_msg)
        await db.flush()   # need model_msg.id for propose_edit's message_id below
        new_messages.append(model_msg)
        contents.append({"role": "model", "parts": parts})

        tool_name = function_call.get("name", "")
        tool_args = function_call.get("args", {}) or {}
        if tool_name == "propose_edit":
            tool_result = await _tool_propose_edit(db, project, tool_args, model_msg.id)
        elif tool_name in _TOOL_IMPLS:
            tool_result = await _TOOL_IMPLS[tool_name](db, project.id, tool_args)
        else:
            tool_result = {"error": f"Unknown tool '{tool_name}'"}

        function_response = {"name": tool_name, "response": tool_result}
        response_msg = ChatMessage(project_id=project.id, role="user", function_response=function_response)
        db.add(response_msg)
        await db.commit()   # release the connection before the next Gemini call
        new_messages.append(response_msg)
        contents.append({"role": "user", "parts": [{"functionResponse": function_response}]})

    # Round cap hit — surface it rather than silently dropping the turn.
    timeout_msg = ChatMessage(
        project_id=project.id, role="model",
        text="I made several tool calls but didn't reach a final answer in time — try rephrasing or asking a narrower question.",
    )
    db.add(timeout_msg)
    await db.commit()
    new_messages.append(timeout_msg)
    return new_messages
