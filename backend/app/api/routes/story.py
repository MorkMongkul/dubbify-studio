"""
app/api/routes/story.py
A project's story notes (app/services/story_notes.py): read them, change
them (the user's edits always win) and start learning from the episodes
marked finished (app/services/story_learning.py). Marking an episode
finished is PATCH /jobs/{id}/finished.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.database import get_db
from app.models.models import Project
from app.schemas.schemas import (
    StoryCharacter, StoryEdit, StoryEpisode, StoryLearning, StoryText, StoryView, StoryWord,
)
from app.services import story_learning, story_notes
from app.services.pipeline_common import episodes as project_episodes

router = APIRouter(prefix="/projects", tags=["story"])


async def _project(db: AsyncSession, project_id: str, *, lock: bool = False) -> Project:
    query = select(Project).where(Project.id == project_id)
    project = (await db.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _view(db: AsyncSession, project: Project) -> StoryView:
    notes = story_notes.normalize(project.story_notes)
    episodes = []
    for number, job in enumerate(await project_episodes(db, project.id), start=1):
        summary = notes["episodes"].get(job.id, {})
        episodes.append(StoryEpisode(
            job_id=job.id, number=number, title=job.title or "",
            finished=job.finished_at is not None, learned=job.id in notes["learned"],
            summary=summary.get("summary", ""), summary_by=summary.get("by", story_notes.AI),
        ))
    return StoryView(
        intro=notes["intro"],
        tone=StoryText(**notes["tone"]),
        characters=[StoryCharacter(**c) for c in notes["characters"]],
        words=[StoryWord(**w) for w in notes["words"]],
        episodes=episodes,
        learning=StoryLearning(**story_learning.learning_state(project.id)),
        can_learn=bool(settings.GEMINI_API_KEY),
    )


@router.get("/{project_id}/story", response_model=StoryView)
async def get_story(project_id: str, db: AsyncSession = Depends(get_db, scope="function")):
    return await _view(db, await _project(db, project_id))


@router.patch("/{project_id}/story", response_model=StoryView)
async def edit_story(project_id: str, payload: StoryEdit, db: AsyncSession = Depends(get_db, scope="function")):
    """Apply what the user changed; changed and new items become theirs, so learning never overrides them."""
    project = await _project(db, project_id, lock=True)
    project.story_notes = story_notes.apply_user_edit(project.story_notes, payload.model_dump(exclude_unset=True))
    flag_modified(project, "story_notes")
    await db.commit()
    await db.refresh(project)
    return await _view(db, project)


@router.post("/{project_id}/story/learn", response_model=StoryView, status_code=status.HTTP_202_ACCEPTED)
async def learn_story(project_id: str, background_tasks: BackgroundTasks,
                      db: AsyncSession = Depends(get_db, scope="function")):
    """Read the finished episodes the notes haven't learned yet (also "Try again" after a failed run)."""
    project = await _project(db, project_id)
    if not settings.GEMINI_API_KEY:
        raise HTTPException(status_code=400, detail="Add a Gemini API key in Settings to learn from episodes.")
    if not story_learning.claim(project.id):
        raise HTTPException(status_code=409, detail="The story notes are already learning from episodes.")
    background_tasks.add_task(story_learning.learn_from_finished, project.id)
    return await _view(db, project)
