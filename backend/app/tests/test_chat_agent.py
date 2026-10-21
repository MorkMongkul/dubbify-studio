"""
Assistant chat: the tool-calling loop, and the auto vs. manual edit-approval
split (Project.chat_edit_mode). Gemini itself is mocked at _call_gemini —
everything downstream (tool execution, DB writes, history replay) is real.
"""
import httpx
import pytest
from sqlalchemy import select

import app.services.chat_agent as chat_agent
from app.models.models import ChatProposedEdit, Job, Project, Segment, Speaker
from app.services import story_notes


async def _get_segment(db_sessionmaker, seg_id: str) -> dict:
    """Direct DB read — there's no GET /segments/{id} endpoint, only
    list-by-job, so assertions here go straight to the DB instead."""
    async with db_sessionmaker() as session:
        seg = (await session.execute(select(Segment).where(Segment.id == seg_id))).scalar_one()
        return {
            "khmer_text": seg.khmer_text,
            "tts_audio_path": seg.tts_audio_path,
            "is_approved": seg.is_approved,
        }



@pytest.fixture(autouse=True)
def gemini_key(monkeypatch):
    """The Assistant checks for a key before saving anything; these tests fake Gemini itself."""
    monkeypatch.setattr(chat_agent.settings, "GEMINI_API_KEY", "test-key")

async def _make_project_with_segment(db_sessionmaker, chat_edit_mode: str = "manual"):
    async with db_sessionmaker() as session:
        project = Project(name="Ep", chat_edit_mode=chat_edit_mode)
        session.add(project)
        await session.flush()
        job = Job(project_id=project.id)
        session.add(job)
        await session.flush()
        seg = Segment(
            job_id=job.id, start_time=0.0, end_time=2.0,
            source_text="我回来了", khmer_text="ខ្ញុំបានត្រឡប់មកវិញ",
        )
        session.add(seg)
        await session.flush()
        project_id, seg_id = project.id, seg.id
        await session.commit()
    return project_id, seg_id


def _text_response(text: str) -> dict:
    return {"parts": [{"text": text}], "role": "model"}


def _function_call_response(name: str, args: dict) -> dict:
    return {"parts": [{"functionCall": {"name": name, "args": args}}], "role": "model"}


@pytest.mark.asyncio
async def test_simple_text_reply_needs_no_tool_call(client, db_sessionmaker, monkeypatch):
    project_id, _ = await _make_project_with_segment(db_sessionmaker)

    async def fake_call_gemini(contents, system=None):
        return _text_response("Sure, happy to help.")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)

    resp = await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Hello"})
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "model"]
    assert messages[1]["text"] == "Sure, happy to help."


@pytest.mark.asyncio
async def test_search_segments_tool_round_trip(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker)

    calls = []

    async def fake_call_gemini(contents, system=None):
        calls.append(contents)
        if len(calls) == 1:
            return _function_call_response("search_segments", {"query": "回来"})
        return _text_response("Found it — episode 1 uses ខ្ញុំបានត្រឡប់មកវិញ for that line.")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)

    resp = await client.post(
        f"/api/v1/chat/{project_id}/messages",
        json={"text": "How was 回来 translated before?"},
    )
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["user", "model", "user", "model"]

    # The tool-response message must carry the REAL search result, not a stub.
    tool_response_msg = messages[2]
    assert tool_response_msg["function_response"]["name"] == "search_segments"
    found = tool_response_msg["function_response"]["response"]["segments"]
    assert any(s["segment_id"] == seg_id for s in found)

    # Second Gemini call must have the full history threaded through, including
    # the tool's real result — proves replay/reconstruction is wired correctly.
    second_call_contents = calls[1]
    assert any("functionResponse" in p for c in second_call_contents for p in c["parts"])


@pytest.mark.asyncio
async def test_propose_edit_manual_mode_stages_without_touching_segment(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker, chat_edit_mode="manual")

    calls = []

    async def fake_call_gemini(contents, system=None):
        calls.append(contents)
        if len(calls) == 1:
            return _function_call_response("propose_edit", {
                "segment_id": seg_id, "new_khmer_text": "ខ្ញុំមកដល់ហើយ", "reason": "more natural",
            })
        return _text_response("Proposed a more natural phrasing — waiting on your approval.")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)

    resp = await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Fix that line"})
    assert resp.status_code == 200

    # Segment must be untouched in manual mode.
    seg = await _get_segment(db_sessionmaker, seg_id)
    assert seg["khmer_text"] == "ខ្ញុំបានត្រឡប់មកវិញ"

    pending = (await client.get(f"/api/v1/chat/{project_id}/pending-edits")).json()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert pending[0]["new_khmer_text"] == "ខ្ញុំមកដល់ហើយ"


@pytest.mark.asyncio
async def test_propose_edit_auto_mode_applies_immediately(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker, chat_edit_mode="auto")

    calls = []

    async def fake_call_gemini(contents, system=None):
        calls.append(contents)
        if len(calls) == 1:
            return _function_call_response("propose_edit", {
                "segment_id": seg_id, "new_khmer_text": "ខ្ញុំមកដល់ហើយ", "reason": "more natural",
            })
        return _text_response("Applied the fix.")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)

    resp = await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Fix that line"})
    assert resp.status_code == 200

    seg = await _get_segment(db_sessionmaker, seg_id)
    assert seg["khmer_text"] == "ខ្ញុំមកដល់ហើយ"
    assert seg["tts_audio_path"] == ""
    assert seg["is_approved"] is False

    pending = (await client.get(f"/api/v1/chat/{project_id}/pending-edits?status=all")).json()
    assert len(pending) == 1
    assert pending[0]["status"] == "approved"
    assert pending[0]["resolved_at"] is not None


@pytest.mark.asyncio
async def test_approve_pending_edit_applies_it(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker, chat_edit_mode="manual")

    async def fake_call_gemini(contents, system=None):
        if len(contents) <= 1:
            return _function_call_response("propose_edit", {
                "segment_id": seg_id, "new_khmer_text": "ខ្ញុំមកដល់ហើយ", "reason": "more natural",
            })
        return _text_response("Waiting on approval.")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)
    await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Fix that line"})

    pending = (await client.get(f"/api/v1/chat/{project_id}/pending-edits")).json()
    edit_id = pending[0]["id"]

    approve_resp = await client.post(f"/api/v1/chat/pending-edits/{edit_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"

    seg = await _get_segment(db_sessionmaker, seg_id)
    assert seg["khmer_text"] == "ខ្ញុំមកដល់ហើយ"

    # Approving twice must fail cleanly rather than double-apply.
    second = await client.post(f"/api/v1/chat/pending-edits/{edit_id}/approve")
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_reject_pending_edit_leaves_segment_untouched(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker, chat_edit_mode="manual")

    async def fake_call_gemini(contents, system=None):
        if len(contents) <= 1:
            return _function_call_response("propose_edit", {
                "segment_id": seg_id, "new_khmer_text": "ខ្ញុំមកដល់ហើយ", "reason": "more natural",
            })
        return _text_response("Waiting on approval.")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)
    await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Fix that line"})

    pending = (await client.get(f"/api/v1/chat/{project_id}/pending-edits")).json()
    edit_id = pending[0]["id"]

    reject_resp = await client.post(f"/api/v1/chat/pending-edits/{edit_id}/reject")
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    seg = await _get_segment(db_sessionmaker, seg_id)
    assert seg["khmer_text"] == "ខ្ញុំបានត្រឡប់មកវិញ"


@pytest.mark.asyncio
async def test_propose_edit_rejects_segment_from_another_project(client, db_sessionmaker, monkeypatch):
    project_id, _ = await _make_project_with_segment(db_sessionmaker, chat_edit_mode="auto")
    _other_project_id, other_seg_id = await _make_project_with_segment(db_sessionmaker, chat_edit_mode="auto")

    async def fake_call_gemini(contents, system=None):
        if len(contents) <= 1:
            return _function_call_response("propose_edit", {
                "segment_id": other_seg_id, "new_khmer_text": "hijacked", "reason": "x",
            })
        return _text_response("done")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)
    await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "edit it"})

    seg = await _get_segment(db_sessionmaker, other_seg_id)
    assert seg["khmer_text"] != "hijacked"


@pytest.mark.asyncio
async def test_tool_round_cap_surfaces_instead_of_hanging(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker)
    monkeypatch.setattr(chat_agent.settings, "CHAT_MAX_TOOL_ROUNDS", 2)

    async def always_calls_tool(contents, system=None):
        return _function_call_response("list_episodes", {})

    monkeypatch.setattr(chat_agent, "_call_gemini", always_calls_tool)

    resp = await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "loop forever"})
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert "didn't reach a final answer" in messages[-1]["text"]


def _shape(contents: list) -> list[str]:
    """Each Gemini turn as role:kind, e.g. 'model:call' for a tool call."""
    def kind(content):
        parts = content["parts"]
        if any("functionCall" in p for p in parts):
            return "call"
        if any("functionResponse" in p for p in parts):
            return "result"
        return "text"
    return [f"{c['role']}:{kind(c)}" for c in contents]


@pytest.mark.asyncio
async def test_the_turn_after_a_tool_call_replays_history_in_order(client, db_sessionmaker, monkeypatch):
    """A tool call and its result are saved within the same second; history must still replay call-first."""
    project_id, _ = await _make_project_with_segment(db_sessionmaker)
    calls = []

    async def fake_call_gemini(contents, system=None):
        calls.append(contents)
        if len(calls) == 1:
            return _function_call_response("search_segments", {"query": "回来"})
        return _text_response("Done.")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)
    first = await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Find 回来"})
    thread_id = first.json()["thread_id"]
    await client.post(f"/api/v1/chat/{project_id}/messages",
                      json={"text": "Thanks, and the next one?", "thread_id": thread_id})

    assert _shape(calls[-1]) == ["user:text", "model:call", "user:result", "model:text", "user:text"]
    # The conversation shown in the panel is in the same order.
    history = (await client.get(f"/api/v1/chat/threads/{thread_id}/messages")).json()
    shown = ["call" if m.get("function_call") else "result" if m.get("function_response") else m["role"] for m in history]
    assert shown == ["user", "call", "result", "model", "user", "model"]


@pytest.mark.asyncio
async def test_assistant_system_text_has_the_notes(client, db_sessionmaker, monkeypatch):
    project_id, _ = await _make_project_with_segment(db_sessionmaker)
    systems: list = []

    async def fake_call_gemini(contents, system=None):
        systems.append(system)
        return _text_response("OK")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)
    await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Hi"})
    assert systems[-1] == chat_agent._SYSTEM_INSTRUCTION

    async with db_sessionmaker() as s:
        project = await s.get(Project, project_id)
        project.story_notes = story_notes.with_intro(None, "A hospital drama in Shanghai.")
        await s.commit()
    await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Hi again"})
    assert systems[-1].startswith(chat_agent._SYSTEM_INSTRUCTION)
    assert "A hospital drama in Shanghai." in systems[-1]


# ── "This line": a message about the line being edited ────────────────────────

@pytest.mark.asyncio
async def test_a_message_about_a_line_carries_it(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker)
    async with db_sessionmaker() as s:
        speaker = Speaker(project_id=project_id, label="SPEAKER_03", display_name="Dr. Wang")
        s.add(speaker)
        await s.flush()
        (await s.get(Segment, seg_id)).speaker_id = speaker.id
        await s.commit()
    seen: list = []

    async def fake_call_gemini(contents, system=None):
        seen.append(contents)
        return _text_response("Done")

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)
    resp = await client.post(f"/api/v1/chat/{project_id}/messages",
                             json={"text": "make this less formal", "line_id": seg_id})
    assert resp.status_code == 200
    ref = resp.json()["messages"][0]["line_ref"]
    assert (ref["segment_id"], ref["episode"], ref["speaker"]) == (seg_id, 1, "Dr. Wang")
    assert (ref["source_text"], ref["khmer_text"]) == ("我回来了", "ខ្ញុំបានត្រឡប់មកវិញ")
    sent = seen[0][-1]["parts"][0]["text"]
    assert sent.startswith("[This line — episode 1, 00:00.0, Dr. Wang (SPEAKER_03), segment_id ")
    assert sent.endswith("]\nmake this less formal")

    # The next turn replays it the same way, so the chat still knows which line was meant
    await client.post(f"/api/v1/chat/{project_id}/messages",
                      json={"text": "and shorter", "thread_id": resp.json()["thread_id"]})
    assert seen[1][0]["parts"][0]["text"].startswith("[This line — episode 1")


@pytest.mark.asyncio
async def test_a_line_from_another_project_is_refused(client, db_sessionmaker, monkeypatch):
    _, other_line = await _make_project_with_segment(db_sessionmaker)
    project_id, _ = await _make_project_with_segment(db_sessionmaker)

    async def must_not_run(contents, system=None):
        raise AssertionError("Gemini was called")

    monkeypatch.setattr(chat_agent, "_call_gemini", must_not_run)
    resp = await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "fix this", "line_id": other_line})
    assert resp.status_code == 404
    assert (await client.get(f"/api/v1/chat/{project_id}/threads")).json() == []


@pytest.mark.asyncio
async def test_a_deleted_line_is_refused(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker)
    async with db_sessionmaker() as s:
        await s.delete(await s.get(Segment, seg_id))
        await s.commit()
    resp = await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "fix this", "line_id": seg_id})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "That line no longer exists"


@pytest.mark.asyncio
async def test_an_answer_cut_off_by_the_output_limit_is_an_error_not_an_empty_reply(client, db_sessionmaker, monkeypatch):
    project_id, _ = await _make_project_with_segment(db_sessionmaker)
    monkeypatch.setattr(chat_agent.settings, "GEMINI_API_KEY", "test-key")
    payloads: list = []

    async def fake_generate(purpose, payload, *, timeout):
        payloads.append(payload)
        # What Gemini sends when its thinking used up the output limit
        return httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]})

    monkeypatch.setattr(chat_agent.gemini_models, "generate_content", fake_generate)
    resp = await client.post(f"/api/v1/chat/{project_id}/messages", json={"text": "Fix line 3"})
    assert resp.status_code == 502
    assert "cut off" in resp.json()["detail"]
    # Thinking counts toward the output limit, so the limit leaves it room
    assert payloads[0]["generationConfig"]["maxOutputTokens"] >= 8192


@pytest.mark.asyncio
async def test_a_suggested_edit_says_which_line_it_is_for(client, db_sessionmaker):
    project_id, seg_id = await _make_project_with_segment(db_sessionmaker)
    async with db_sessionmaker() as s:
        seg = await s.get(Segment, seg_id)
        s.add(ChatProposedEdit(project_id=project_id, segment_id=seg_id, old_khmer_text="a", new_khmer_text="b", reason="r"))
        await s.commit()
        job_id = seg.job_id

    edit = (await client.get(f"/api/v1/chat/{project_id}/pending-edits")).json()[0]
    assert edit["line"] == {"job_id": job_id, "episode": 1, "time": 0.0, "source_text": "我回来了"}
