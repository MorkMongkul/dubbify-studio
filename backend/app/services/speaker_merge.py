"""Same person as…: one character under two labels becomes one (POST /speakers/{id}/merge, and the characters
upgrade's same-voice merges, Ruling AQ)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.models import Project, Segment, Speaker
from app.services import story_notes


async def merge_into(db: AsyncSession, source: Speaker, target: Speaker) -> tuple[list[Segment], list[str]]:
    """Moves `source`'s lines, in every episode, to `target` (same project), links the Story characters linked to
    its label to `target`'s, and deletes `source`. Lines already voiced keep their audio until voiced again, as when
    a speaker's voice changes. The caller commits. Returns (the lines moved, the Story characters relinked) — what
    /unmerge needs, with a snapshot of `source` taken before."""
    lines = (await db.execute(select(Segment).where(Segment.speaker_id == source.id))).scalars().all()
    for line in lines:
        line.speaker_id = target.id
    project = await db.get(Project, source.project_id)
    project.story_notes, relinked = story_notes.relink_speaker(project.story_notes, source.label, target.label)
    flag_modified(project, "story_notes")
    await db.flush()
    await db.delete(source)
    return lines, relinked
