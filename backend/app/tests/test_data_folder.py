"""
When the desktop app moves its data folder (the Movelo rename changed the app
ID, and with it ~/Library/Application Support/<app id>), every media path the
database holds still points into the old folder. The backend rewrites them to
the new folder once — only values inside the old folder, and running it again
changes nothing.
"""
import pytest
from sqlalchemy import select, update

from app.core.data_folder import follow_moved_data_folder
from app.models.models import Job, Overlay, OverlayTemplate, Project, Segment, Speaker, Voice

OLD = "/Users/sokhà/Library/Application Support/studio.dubify.desktop"
NEW = "/Users/sokhà/Library/Application Support/com.movelo.studio"


async def _seed(session_factory):
    async with session_factory() as db:
        project = Project(name="Ep", logo_path=f"{OLD}/uploads/p1/logo.png")
        db.add(project)
        await db.flush()
        job = Job(
            project_id=project.id,
            video_path=f"{OLD}/uploads/p1/j1/source.mp4",
            audio_path=f"{OLD}/uploads/p1/j1/audio.wav",
            output_path="",
            subtitle_path="uploads/p1/j1/subs.srt",                       # relative: not ours to touch
        )
        voice = Voice(name="Narrator", reference_audio_path=f"{OLD}/uploads/voices/v1/ref.wav")
        db.add_all([job, voice])
        await db.flush()
        speaker = Speaker(project_id=project.id, label="SPEAKER_00",
                          reference_audio_path=f"{OLD}-backup/uploads/x.wav")   # a different folder
        db.add(speaker)
        await db.flush()
        db.add_all([
            Segment(job_id=job.id, start_time=0, end_time=1,
                    tts_audio_path=f"{OLD}/uploads/p1/j1/tts/seg_1_raw.wav"),
            Segment(job_id=job.id, start_time=1, end_time=2),
            Overlay(job_id=job.id, type="image", media_path=f"{OLD}/uploads/p1/j1/overlays/a.png"),
            OverlayTemplate(name="Logo", items=[
                {"type": "image", "media_path": f"{OLD}/uploads/templates/t1/a.png", "x": 0.1},
                {"type": "subtitle", "text": f"Made in {OLD}/uploads/ (text, not a path)"},
            ]),
        ])
        await db.flush()
        # A segment with no audio yet holds NULL (the column default only fills new rows).
        await db.execute(update(Segment).where(Segment.start_time == 1).values(tts_audio_path=None))
        await db.commit()
        return project.id, job.id, voice.id, speaker.id


@pytest.mark.asyncio
async def test_paths_inside_the_old_folder_move_to_the_new_one(db_sessionmaker):
    project_id, job_id, voice_id, speaker_id = await _seed(db_sessionmaker)

    changed = await follow_moved_data_folder(OLD, NEW, session_factory=db_sessionmaker)
    assert changed == 7

    async with db_sessionmaker() as db:
        project = await db.get(Project, project_id)
        job = await db.get(Job, job_id)
        voice = await db.get(Voice, voice_id)
        speaker = await db.get(Speaker, speaker_id)
        segments = (await db.execute(select(Segment).order_by(Segment.start_time))).scalars().all()
        overlay = (await db.execute(select(Overlay))).scalar_one()
        template = (await db.execute(select(OverlayTemplate))).scalar_one()

    assert project.logo_path == f"{NEW}/uploads/p1/logo.png"
    assert job.video_path == f"{NEW}/uploads/p1/j1/source.mp4"
    assert job.audio_path == f"{NEW}/uploads/p1/j1/audio.wav"
    assert job.subtitle_path == "uploads/p1/j1/subs.srt"
    assert voice.reference_audio_path == f"{NEW}/uploads/voices/v1/ref.wav"
    assert speaker.reference_audio_path == f"{OLD}-backup/uploads/x.wav"
    assert [s.tts_audio_path for s in segments] == [f"{NEW}/uploads/p1/j1/tts/seg_1_raw.wav", None]
    assert overlay.media_path == f"{NEW}/uploads/p1/j1/overlays/a.png"
    assert template.items[0]["media_path"] == f"{NEW}/uploads/templates/t1/a.png"
    assert template.items[1]["text"] == f"Made in {OLD}/uploads/ (text, not a path)"


@pytest.mark.asyncio
async def test_running_it_again_changes_nothing(db_sessionmaker):
    await _seed(db_sessionmaker)
    await follow_moved_data_folder(OLD, NEW, session_factory=db_sessionmaker)
    assert await follow_moved_data_folder(OLD, NEW, session_factory=db_sessionmaker) == 0
    # A trailing slash on either folder means the same folder.
    assert await follow_moved_data_folder(OLD + "/", NEW + "/", session_factory=db_sessionmaker) == 0


@pytest.mark.asyncio
async def test_same_folder_is_a_no_op(db_sessionmaker):
    await _seed(db_sessionmaker)
    assert await follow_moved_data_folder(OLD, OLD + "/", session_factory=db_sessionmaker) == 0
    assert await follow_moved_data_folder("", NEW, session_factory=db_sessionmaker) == 0


@pytest.mark.asyncio
async def test_startup_follows_a_moved_folder_and_survives_a_failure(monkeypatch):
    from app import main
    calls = []

    async def fake_follow(previous, current):
        calls.append((previous, current))
        raise RuntimeError("disk full")

    async def nothing():
        return None

    monkeypatch.setattr(main, "follow_moved_data_folder", fake_follow)
    monkeypatch.setattr(main, "init_db", nothing)
    monkeypatch.setattr(main, "recover_orphaned_jobs", nothing)
    monkeypatch.setattr(main, "seed_builtin_voices", nothing)
    monkeypatch.setattr(main.line_timing, "ensure_model", nothing)   # never downloads the speech model
    monkeypatch.setattr(main.settings, "DUBIFY_DATA_DIR", NEW)
    monkeypatch.setattr(main.settings, "DUBIFY_PREVIOUS_DATA_DIR", OLD)

    async with main.lifespan(main.app):     # a failure is logged, never fatal
        pass
    assert calls == [(OLD, NEW)]

    calls.clear()
    monkeypatch.setattr(main.settings, "DUBIFY_PREVIOUS_DATA_DIR", "")
    async with main.lifespan(main.app):
        pass
    assert calls == []
