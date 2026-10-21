"""
app/api/routes/chat.py
Assistant chat endpoints — one ongoing conversation per project, plus review
of the edits it proposes. See app/services/chat_agent.py for the actual
Gemini tool-calling loop.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import ChatMessage, ChatProposedEdit, Project, Segment
from app.schemas.schemas import (
    ChatMessageResponse, ChatProposedEditResponse, ChatSendRequest, ChatSendResponse,
)
from app.services.chat_agent import LineNotFound, apply_edit_to_segment, message_order, naive_utcnow, run_chat_turn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["Assistant Chat"])


async def _get_project(project_id: str, db: AsyncSession) -> Project:
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/messages", response_model=list[ChatMessageResponse])
async def list_messages(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    await _get_project(project_id, db)
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.project_id == project_id).order_by(*message_order())
    )
    return result.scalars().all()


@router.post("/{project_id}/messages", response_model=ChatSendResponse)
async def send_message(project_id: str, payload: ChatSendRequest, db: AsyncSession = Depends(get_db, scope="function")):
    project = await _get_project(project_id, db)
    try:
        new_messages = await run_chat_turn(db, project, payload.text.strip(), line_id=payload.line_id)
    except LineNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        logger.error(f"Assistant chat turn failed for project {project_id[:8]}: {e}")
        raise HTTPException(status_code=502, detail=str(e))
    return ChatSendResponse(messages=new_messages)


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
