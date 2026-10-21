"""
Chat threads: a project has several Assistant chats, each remembering only its
own messages; the chat a project had before threads becomes its first one.
"""
import pytest
from sqlalchemy import select

from app.models.models import ChatMessage, ChatProposedEdit, ChatThread, Job, Project, Segment
from app.services import chat_agent


async def _project(db_sessionmaker) -> tuple[str, str]:
    """A project with one line; returns (project id, segment id)."""
    async with db_sessionmaker() as s:
        project = Project(name="Hospital Hearts")
        s.add(project)
        await s.flush()
        job = Job(project_id=project.id)
        s.add(job)
        await s.flush()
        seg = Segment(job_id=job.id, start_time=0.0, end_time=2.0, source_text="你好", khmer_text="សួស្តី")
        s.add(seg)
        await s.commit()
        return project.id, seg.id


def _fake(monkeypatch, reply: str = "OK") -> list:
    seen: list = []

    async def fake_call_gemini(contents, system=None):
        seen.append(contents)
        return {"parts": [{"text": reply}], "role": "model"}

    monkeypatch.setattr(chat_agent, "_call_gemini", fake_call_gemini)
    return seen


def _texts(contents: list) -> list[str]:
    return [p.get("text", "") for c in contents for p in c["parts"]]


async def _send(client, project_id: str, text: str, thread_id: str | None = None):
    body = {"text": text} | ({"thread_id": thread_id} if thread_id else {})
    return await client.post(f"/api/v1/chat/{project_id}/messages", json=body)


def test_thread_title():
    assert chat_agent.thread_title("  How was 老公 translated\nin episode 2?  ") == "How was 老公 translated in episode 2?"
    long = chat_agent.thread_title("Make " + "very " * 30 + "long")
    assert len(long) <= 60 and long.endswith("…")
    assert chat_agent.thread_title("   ") == "New chat"


@pytest.mark.asyncio
async def test_the_first_message_starts_a_titled_chat(client, db_sessionmaker, monkeypatch):
    project_id, _ = await _project(db_sessionmaker)
    _fake(monkeypatch)
    resp = await _send(client, project_id, "How was 老公 translated in episode 2?")
    assert resp.status_code == 200
    thread_id = resp.json()["thread_id"]
    assert {m["thread_id"] for m in resp.json()["messages"]} == {thread_id}

    threads = (await client.get(f"/api/v1/chat/{project_id}/threads")).json()
    assert [(t["id"], t["title"], t["message_count"]) for t in threads] == [
        (thread_id, "How was 老公 translated in episode 2?", 2)]


@pytest.mark.asyncio
async def test_a_chat_only_remembers_its_own_messages(client, db_sessionmaker, monkeypatch):
    project_id, _ = await _project(db_sessionmaker)
    seen = _fake(monkeypatch)
    doctor = (await _send(client, project_id, "About the doctor")).json()["thread_id"]
    nurse = (await _send(client, project_id, "About the nurse")).json()["thread_id"]
    assert doctor != nurse

    await _send(client, project_id, "And his name?", doctor)
    assert "About the doctor" in _texts(seen[-1]) and "About the nurse" not in _texts(seen[-1])
    shown = (await client.get(f"/api/v1/chat/threads/{nurse}/messages")).json()
    assert [m["text"] for m in shown] == ["About the nurse", "OK"]


@pytest.mark.asyncio
async def test_earlier_messages_become_the_first_chat(client, db_sessionmaker):
    project_id, _ = await _project(db_sessionmaker)
    async with db_sessionmaker() as s:
        s.add(ChatMessage(project_id=project_id, role="user", text="Old question"))
        await s.flush()
        s.add(ChatMessage(project_id=project_id, role="model", text="Old answer"))
        await s.commit()

    threads = (await client.get(f"/api/v1/chat/{project_id}/threads")).json()
    assert [(t["title"], t["message_count"]) for t in threads] == [("Old question", 2)]
    again = (await client.get(f"/api/v1/chat/{project_id}/threads")).json()
    assert [t["id"] for t in again] == [threads[0]["id"]]
    shown = (await client.get(f"/api/v1/chat/threads/{threads[0]['id']}/messages")).json()
    assert [m["text"] for m in shown] == ["Old question", "Old answer"]


@pytest.mark.asyncio
async def test_deleting_a_chat_keeps_its_pending_edits(client, db_sessionmaker, monkeypatch):
    project_id, seg_id = await _project(db_sessionmaker)
    _fake(monkeypatch)
    thread_id = (await _send(client, project_id, "hi")).json()["thread_id"]
    async with db_sessionmaker() as s:
        msg = (await s.execute(select(ChatMessage).where(ChatMessage.thread_id == thread_id))).scalars().first()
        s.add(ChatProposedEdit(project_id=project_id, segment_id=seg_id, message_id=msg.id,
                               old_khmer_text="a", new_khmer_text="b", reason="r"))
        await s.commit()

    assert (await client.delete(f"/api/v1/chat/threads/{thread_id}")).status_code == 204
    assert (await client.get(f"/api/v1/chat/{project_id}/threads")).json() == []
    pending = (await client.get(f"/api/v1/chat/{project_id}/pending-edits")).json()
    assert len(pending) == 1 and pending[0]["message_id"] is None


@pytest.mark.asyncio
async def test_a_chat_of_another_project_is_refused(client, db_sessionmaker, monkeypatch):
    first, _ = await _project(db_sessionmaker)
    second, _ = await _project(db_sessionmaker)
    _fake(monkeypatch)
    thread_id = (await _send(client, first, "hi")).json()["thread_id"]

    assert (await _send(client, second, "hi", thread_id)).status_code == 404
    assert (await client.get("/api/v1/chat/threads/no-such-chat/messages")).status_code == 404
    assert (await client.delete("/api/v1/chat/threads/no-such-chat")).status_code == 404


@pytest.mark.asyncio
async def test_chats_are_listed_newest_first_with_counts(client, db_sessionmaker, monkeypatch):
    project_id, _ = await _project(db_sessionmaker)
    _fake(monkeypatch)
    first = (await _send(client, project_id, "first chat")).json()["thread_id"]
    second = (await _send(client, project_id, "second chat")).json()["thread_id"]
    await _send(client, project_id, "more", first)

    threads = (await client.get(f"/api/v1/chat/{project_id}/threads")).json()
    assert [(t["id"], t["message_count"]) for t in threads] == [(first, 4), (second, 2)]


@pytest.mark.asyncio
async def test_deleting_a_project_deletes_its_chats(client, db_sessionmaker, monkeypatch):
    project_id, _ = await _project(db_sessionmaker)
    _fake(monkeypatch)
    await _send(client, project_id, "hi")
    assert (await client.delete(f"/api/v1/projects/{project_id}")).status_code == 204
    async with db_sessionmaker() as s:
        assert (await s.execute(select(ChatThread))).scalars().all() == []
