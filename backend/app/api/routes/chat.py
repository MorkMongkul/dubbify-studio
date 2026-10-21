"""
app/api/routes/chat.py
Assistant chat endpoints — a project's chats (threads), each remembering only
its own messages, plus review of the edits the Assistant proposes (those
belong to the project, whichever chat they came from). See
app/services/chat_agent.py for the actual Gemini tool-calling loop.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import ChatMessage, ChatProposedEdit, ChatThread, Project, Segment
from app.schemas.schemas import (
    ChatMessageResponse, ChatProposedEditResponse, ChatSendRequest, ChatSendResponse, ChatThreadResponse,
)
from app.services.chat_agent import (
    LineNotFound, ThreadNotFound, adopt_unthreaded, apply_edit_to_segment, message_order, naive_utcnow, run_chat_turn,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Assistant Chat"])


async def _get_project(project_id: str, db: AsyncSession) -> Project:
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _get_thread(thread_id: str, db: AsyncSession) -> ChatThread:
    thread = await db.get(ChatThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="That chat no longer exists")
    return thread


@router.get("/{project_id}/threads", response_model=list[ChatThreadResponse])
async def list_threads(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """The project's chats, the one used last first."""
    await _get_project(project_id, db)
    await adopt_unthreaded(db, project_id)
    shown = (
        select(ChatMessage.thread_id, func.count().label("n"))
        .where(ChatMessage.function_call.is_(None), ChatMessage.function_response.is_(None), ChatMessage.text != "")
        .group_by(ChatMessage.thread_id).subquery()
    )
    rows = await db.execute(
        select(ChatThread, func.coalesce(shown.c.n, 0))
        .outerjoin(shown, shown.c.thread_id == ChatThread.id)
        .where(ChatThread.project_id == project_id)
        .order_by(ChatThread.updated_at.desc())
    )
    return [
        ChatThreadResponse(id=t.id, project_id=t.project_id, title=t.title or "", created_at=t.created_at,
                           updated_at=t.updated_at, message_count=n)
        for t, n in rows.all()
    ]


@router.get("/threads/{thread_id}/messages", response_model=list[ChatMessageResponse])
async def list_messages(thread_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    thread = await _get_thread(thread_id, db)
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.thread_id == thread.id).order_by(*message_order())
    )
    return result.scalars().all()


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    """Delete a chat and its messages; the edits it proposed stay with the project."""
    thread = await _get_thread(thread_id, db)
    in_chat = select(ChatMessage.id).where(ChatMessage.thread_id == thread.id)
    await db.execute(update(ChatProposedEdit).where(ChatProposedEdit.message_id.in_(in_chat)).values(message_id=None))
    await db.execute(delete(ChatMessage).where(ChatMessage.thread_id == thread.id))
    await db.delete(thread)
    await db.commit()


@router.post("/{project_id}/messages", response_model=ChatSendResponse)
async def send_message(project_id: str, payload: ChatSendRequest, db: AsyncSession = Depends(get_db, scope="function")):
    project = await _get_project(project_id, db)
    try:
        thread_id, new_messages = await run_chat_turn(
            db, project, payload.text.strip(), thread_id=payload.thread_id, line_id=payload.line_id)
    except (LineNotFound, ThreadNotFound) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        logger.error(f"Assistant chat turn failed for project {project_id[:8]}: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    return ChatSendResponse(thread_id=thread_id, messages=new_messages)


@router.get("/{project_id}/pending-edits", response_model=list[ChatProposedEditResponse])
async def list_pending_edits(project_id: str, status: str = "pending", db: AsyncSession = Depends(get_db, scope="function")):
    await _get_project(project_id, db)
    stmt = select(ChatProposedEdit).where(ChatProposedEdit.project_id == project_id)
    if status != "all":
        stmt = stmt.where(ChatProposedEdit.status == status)
    stmt = stmt.order_by(ChatProposedEdit.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


async def _resolve_edit(edit_id: str, db: AsyncSession) -> ChatProposedEdit:
    edit = (await db.execute(select(ChatProposedEdit).where(ChatProposedEdit.id == edit_id))).scalar_one_or_none()
    if not edit:
        raise HTTPException(status_code=404, detail="Proposed edit not found")
    if edit.status != "pending":
        raise HTTPException(status_code=409, detail=f"This edit was already {edit.status}")
    return edit


@router.post("/pending-edits/{edit_id}/approve", response_model=ChatProposedEditResponse)
async def approve_edit(edit_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    edit = await _resolve_edit(edit_id, db)
    seg = (await db.execute(select(Segment).where(Segment.id == edit.segment_id))).scalar_one_or_none()
    if not seg:
        raise HTTPException(status_code=404, detail="The segment this edit targets no longer exists")

    apply_edit_to_segment(seg, edit.new_khmer_text)
    edit.status = "approved"
    edit.resolved_at = naive_utcnow()
    await db.commit()
    await db.refresh(edit)
    return edit


@router.post("/pending-edits/{edit_id}/reject", response_model=ChatProposedEditResponse)
async def reject_edit(edit_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    edit = await _resolve_edit(edit_id, db)
    edit.status = "rejected"
    edit.resolved_at = naive_utcnow()
    await db.commit()
    await db.refresh(edit)
    return edit
