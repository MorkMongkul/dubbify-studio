#!/usr/bin/env python3
"""How well Movelo recognises characters on a real project, measured on a COPY of the app's data folder.

  cd backend && venv/bin/python -I ../scripts/measure-characters.py <copy> <project id> \\
      [--episodes N] [--corrections <file.json>] [--after-merges]

<copy> holds dubify.db, made from the app's database with sqlite3's `.backup` (never opened for writing in the
app's own folder), and uploads/<project id>/ cloned from the app's (`/bin/cp -Rc`). This script writes into the copy,
so it refuses the app's own folder.

It runs the shipped engine in the app's order:
  1. the upgrade (upgrade.convert_project, once per project, as the app's start-up does before the project's prints):
     each line goes to the character of the voice it was made with — its own voice, else the voice its Khmer audio
     clearly sounds like (the voice model hears it; a few minutes on a big project);
  2. prints for every line of the project that has none from today's models (prints.prints_for_spans), saved into
     the copy's database, so a later run, or a backend started on the copy, reuses them (about a minute per episode
     with faces on an M1);
  3. "as it happens": episode N (in the order they were added) knows only episodes 1..N-1 — the profiles come from
     their confirmed lines (store.is_confirmed, decide.profile_from) and each line of N gets decide(), told as the
     app tells it whether another line of N is spoken over it (decide.overlapping).

Truth: the Khmer voice of each line's character after the upgrade — the voice the line was made with (one voice is
one actor for the whole series, so a guess of another label cast with the same voice is right), or the character a corrections file names:
JSON {segment id: speaker label}, for answer-key mistakes the owner pointed out. A line counts once its true
character has a profile (so never in the first episode). --episodes N measures only the first N episodes (the ones
the owner checked); later ones are neither measured nor learned from, but still get their prints.

The upgrade leaves one actor under several labels where Gemini gave them several (each its own profile), and asks
the owner about them with "Same character?". So it is measured twice: as the upgrade leaves the characters, and with
the pairs "Same character?" offers for a shared voice merged (suggestions.find's rule, as Merge would: the one with
more lines stays). Prints the overall right %, how many lines are "sure" and how often those are right, and a table
per episode. Exits 1 below 68% right, or sure on fewer than 50% of lines, or sure lines right below 83% — judged as
the upgrade leaves the characters, or after those merges with --after-merges."""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

RIGHT_TARGET, SURE_TARGET, SURE_RIGHT_TARGET = 0.68, 0.50, 0.83
APP_FOLDERS = ("com.movelo.studio", "studio.dubify.desktop")


@dataclass
class Line:
    id: str
    truth: str | None      # the character's speaker id (corrections applied)
    voice: str | None      # that character's Khmer voice: the identity "right" is judged by
    confirmed: bool
    prints: object         # LinePrints
    span: tuple[float, float]   # where its actor spoke (timeline.actor_span)


@dataclass
class Episode:
    title: str
    lines: list[Line] = field(default_factory=list)


@dataclass
class Data:
    episodes: list[Episode]
    voice_of: dict[str, str | None]   # speaker id -> its Khmer voice
    merged_into: dict[str, str]       # speaker id -> the character it is after the same-voice merges


def _setup(copy: Path) -> None:
    """Point the backend at the copy before anything imports it: the copy's database and uploads, no AI keys."""
    os.environ.update({"DUBIFY_DATA_DIR": str(copy), "DATABASE_URL": "", "UPLOAD_DIR": str(copy / "uploads"),
                       "GEMINI_API_KEY": "", "HF_TOKEN": "", "VOXCPM2_API_URL": ""})
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))


async def _upgrade(project_id: str) -> str:
    from app.core import database
    from app.models.models import Project
    from app.services.characters import upgrade
    async with database.AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        if project is None:
            raise SystemExit(f"No project {project_id} in the copy")
        version = project.characters_version
    if version is not None and version >= upgrade.CHARACTERS_VERSION:
        return f"already upgraded (version {version})"
    began = time.monotonic()
    moved = await upgrade.convert_project(database.AsyncSessionLocal, project_id)
    async with database.AsyncSessionLocal() as db:   # a failed conversion (logged) leaves it where it was
        now = (await db.get(Project, project_id)).characters_version
    return (f"version {version} -> {now}: {moved} line(s) moved to the character of the voice they were made with, "
            f"in {time.monotonic() - began:.0f} s")


async def _jobs(db, project_id: str):
    from sqlalchemy import select
    from app.models.models import Job
    return (await db.execute(select(Job).where(Job.project_id == project_id)
                             .order_by(Job.created_at, Job.title))).scalars().all()


async def _make_prints(project_id: str) -> list[str]:
    """Prints for the project's lines that have none from today's models, episode by episode, saved."""
    from sqlalchemy import select
    from app.core import database
    from app.core.paths import resolve_media_path
    from app.models.models import LinePrint, Segment
    from app.services.characters import prints, speed, store
    from app.services.timeline import actor_span

    report = []
    async with database.AsyncSessionLocal() as db:
        jobs = await _jobs(db, project_id)
    with_faces = None
    for job in jobs:
        async with database.AsyncSessionLocal() as db:
            todo = (await db.execute(
                select(Segment).outerjoin(LinePrint, LinePrint.segment_id == Segment.id)
                .where(Segment.job_id == job.id, (LinePrint.segment_id.is_(None))
                       | (LinePrint.model_version != store.model_version()))
                .order_by(Segment.start_time))).scalars().all()
            if not todo:
                continue
            vocals = store._vocals_of(job)
            if vocals is None:
                report.append(f"{job.title}: no vocals.wav in the copy, {len(todo)} line(s) without prints")
                continue
            if with_faces is None:
                with_faces = await asyncio.to_thread(speed.face_step_enabled)
                print(f"Faces: {'on' if with_faces else 'off (voices only)'}, as the app decides on this Mac",
                      flush=True)
            began = time.monotonic()
            found = await asyncio.to_thread(prints.prints_for_spans, resolve_media_path(job.video_path),
                                            str(vocals), [actor_span(s) for s in todo], with_faces)
            await store.save_prints(db, job.id, [s.id for s in todo], found, store.faces_dir(job))
            await db.commit()
            line = (f"{job.title}: {len(todo)} line(s) in {time.monotonic() - began:.0f} s "
                    f"({job.duration_secs or 0:.0f} s of video), {sum(p.face is not None for p in found)} "
                    f"with a speaking face")
            print(f"  prints {line}", flush=True)
            report.append(line)
    return report


def _same_voice_merges(speakers, lines: dict[str, int], dismissed) -> dict[str, str]:
    """Speaker id -> the character it becomes when every "Same character?" pair of a shared voice is merged.
    Only the shared-voice rule: with no profiles given, suggestions.find offers nothing else."""
    from app.services.characters import suggestions
    group = {sp.id: sp.id for sp in speakers}

    def root(x: str) -> str:
        while group[x] != x:
            x = group[x]
        return x

    for pair in suggestions.find(list(speakers), {}, dismissed, lines):
        a, b = root(pair["a"]), root(pair["b"])
        if a != b:   # the one with more lines stays, as Merge keeps it
            keep, other = (b, a) if lines.get(b, 0) > lines.get(a, 0) else (a, b)
            group[other] = keep
    return {sid: root(sid) for sid in group}


async def load(project_id: str, episodes: int | None, corrections: dict[str, str]) -> Data:
    from sqlalchemy import func, select
    from app.core import database
    from app.models.models import Job, LinePrint, Project, Segment, Speaker
    from app.services.characters import store, suggestions
    from app.services.characters.types import LinePrints
    from app.services.timeline import actor_span

    async with database.AsyncSessionLocal() as db:
        project = await db.get(Project, project_id)
        jobs = (await _jobs(db, project_id))[:episodes]
        speakers = (await db.execute(select(Speaker).where(Speaker.project_id == project_id))).scalars().all()
        lines = dict((await db.execute(
            select(Segment.speaker_id, func.count(Segment.id)).join(Job, Job.id == Segment.job_id)
            .where(Job.project_id == project_id).group_by(Segment.speaker_id))).all())
        by_label = {sp.label: sp.id for sp in speakers}
        unknown = sorted(set(corrections.values()) - set(by_label))
        if unknown:
            raise SystemExit(f"The corrections name labels the project doesn't have: {', '.join(unknown)}")
        found = set()
        result = []
        for job in jobs:
            rows = (await db.execute(
                select(Segment, LinePrint).outerjoin(LinePrint, LinePrint.segment_id == Segment.id)
                .where(Segment.job_id == job.id).order_by(Segment.start_time))).all()
            episode = Episode(job.title)
            for seg, row in rows:
                current = row is not None and row.model_version == store.model_version()
                truth = seg.speaker_id
                if seg.id in corrections:
                    truth = by_label[corrections[seg.id]]
                    found.add(seg.id)
                prints = LinePrints(store._vector(row.voice_print, store.VOICE_SIZE),
                                    store._vector(row.face_print, store.FACE_SIZE), row.speaking_score, None) \
                    if current else LinePrints(None, None, None, None)
                episode.lines.append(Line(seg.id, truth, None, store.is_confirmed(seg, job), prints, actor_span(seg)))
            result.append(episode)
    missing = sorted(set(corrections) - found)
    if missing:
        raise SystemExit(f"{len(missing)} corrected line(s) aren't in the measured episodes: {', '.join(missing)}")
    voice_of = {sp.id: sp.voice_id for sp in speakers}
    for episode in result:
        for line in episode.lines:
            line.voice = voice_of.get(line.truth)
    merged = _same_voice_merges(speakers, lines, suggestions.dismissals_from(project.character_dismissals))
    return Data(result, voice_of, merged)


def measure(data: Data, after_merges: bool = False) -> tuple[list[dict], Counter]:
    """Per episode and overall: counted lines, right, sure, sure and right, nobody, new people, same label."""
    from app.services.characters.decide import decide, overlapping, profile_from

    character = (lambda sid: data.merged_into.get(sid, sid)) if after_merges else (lambda sid: sid)
    rows, total = [], Counter()
    for n, episode in enumerate(data.episodes):
        teach: dict[str, list] = {}
        for earlier in data.episodes[:n]:
            for line in earlier.lines:
                if line.confirmed and line.truth:
                    teach.setdefault(character(line.truth), []).append(line.prints)
        profiles = {sid: p for sid, p in ((sid, profile_from(prints)) for sid, prints in teach.items())
                    if p.voice is not None or p.face is not None}
        known_voices = {data.voice_of[sid] for sid in profiles if data.voice_of.get(sid)}
        c = Counter(lines=len(episode.lines))
        mixed = overlapping([line.span for line in episode.lines])
        for line, spoken_over in zip(episode.lines, mixed):
            if line.voice is None:
                c["no answer"] += 1
                continue
            decision = decide(line.prints, profiles, mixed_voice=spoken_over)
            if line.voice not in known_voices:
                c["new person"] += 1
                c["new person, nobody"] += decision.speaker_id is None
                continue
            right = decision.speaker_id is not None and data.voice_of.get(decision.speaker_id) == line.voice
            c["counted"] += 1
            c["right"] += right
            c["same label"] += decision.speaker_id == character(line.truth)
            c["sure"] += decision.sure
            c["sure right"] += decision.sure and right
            c["nobody"] += decision.speaker_id is None
            c["with face"] += line.prints.face is not None
        rows.append({"episode": episode.title, **c})
        total.update(c)
    return rows, total


def _pct(a: int, b: int) -> str:
    return f"{a / b:6.1%}" if b else "     -"


def _met(total: Counter) -> bool:
    n, sure = total["counted"], total["sure"]
    return bool(n) and (total["right"] / n >= RIGHT_TARGET and sure / n >= SURE_TARGET
                        and total["sure right"] / max(1, sure) >= SURE_RIGHT_TARGET)


def report(title: str, rows: list[dict], total: Counter) -> None:
    print(f"\n== {title}")
    print(f"{'episode':<9}{'lines':>6}{'counted':>8}{'right':>8}{'sure':>8}{'sure ok':>9}{'nobody':>7}"
          f"{'new':>5}{'face':>6}")
    for r in rows + [{"episode": "all", **total}]:
        g = Counter(r)
        print(f"{r['episode']:<9}{g['lines']:>6}{g['counted']:>8}{_pct(g['right'], g['counted']):>8}"
              f"{_pct(g['sure'], g['counted']):>8}{_pct(g['sure right'], g['sure']):>9}{g['nobody']:>7}"
              f"{g['new person']:>5}{g['with face']:>6}")
    n, right, sure, sure_right = total["counted"], total["right"], total["sure"], total["sure right"]
    if not n:
        print("No line could be measured (the first episode has no profiles to compare with).")
        return
    print(f"Overall right: {right / n:.1%} ({right}/{n})   target >= {RIGHT_TARGET:.0%}")
    print(f"Sure: {sure / n:.1%} of lines ({sure}/{n}), right on {sure_right / max(1, sure):.1%} "
          f"({sure_right}/{sure})   targets >= {SURE_TARGET:.0%} at >= {SURE_RIGHT_TARGET:.0%}")
    print(f"To check: {n - sure} line(s), {total['nobody']} of them given to nobody; the very label too (not only "
          f"the actor): {total['same label'] / n:.1%}; lines of a person not seen before: {total['new person']} "
          f"({total['new person, nobody']} given to nobody)")


async def run(project_id: str, episodes: int | None, corrections: dict[str, str], after_merges: bool) -> bool:
    from app.core.database import init_db
    from app.services.characters import decide, store

    began = time.monotonic()
    await init_db()   # the copy's tables brought up to this version, as the app's start-up does
    print(f"Upgrade: {await _upgrade(project_id)}", flush=True)
    made = await _make_prints(project_id)
    print(f"Prints (models {store.model_version()}): "
          f"{'made for ' + str(len(made)) + ' episode(s)' if made else 'all saved before'} "
          f"in {time.monotonic() - began:.0f} s", flush=True)
    data = await load(project_id, episodes, corrections)
    merges = sum(1 for sid, into in data.merged_into.items() if sid != into)
    print(f"Measured: {', '.join(e.title for e in data.episodes)} as it happens; corrections: {len(corrections)}; "
          f"floors voice {decide.UNKNOWN_VOICE_FLOOR}, face {decide.UNKNOWN_FACE_FLOOR}; "
          f"sure margin {decide.SURE_VOICE_MARGIN}")
    as_upgraded = measure(data)
    merged = measure(data, after_merges=True)
    report("As the upgrade leaves the characters" + ("" if after_merges else " (judged)"), *as_upgraded)
    report(f"After merging the {merges} character(s) \"Same character?\" pairs by a shared voice"
           + (" (judged)" if after_merges else ""), *merged)
    met = _met((merged if after_merges else as_upgraded)[1])
    print(f"\nTargets {'met' if met else 'NOT met'} ({time.monotonic() - began:.0f} s)")
    return met


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("copy", type=Path, help="a copy of the app's data folder (dubify.db + uploads/<project>)")
    parser.add_argument("project", help="the project's id")
    parser.add_argument("--episodes", type=int, help="measure only the first N episodes (the checked ones)")
    parser.add_argument("--corrections", type=Path, help="JSON {segment id: speaker label}")
    parser.add_argument("--after-merges", action="store_true",
                        help="judge the targets after the shared-voice \"Same character?\" merges")
    parser.add_argument("-v", "--verbose", action="store_true", help="the engine's own log lines too")
    args = parser.parse_args()

    copy = args.copy.expanduser().resolve()
    if any(part in APP_FOLDERS for part in copy.parts):
        print(f"{copy} looks like the app's own data folder: this script writes, so give it a copy")
        return 2
    if not (copy / "dubify.db").is_file() or not (copy / "uploads" / args.project).is_dir():
        print(f"{copy} needs dubify.db and uploads/{args.project}/")
        return 2
    corrections = json.loads(args.corrections.read_text()) if args.corrections else {}
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR, format="%(levelname)s %(message)s")
    logging.getLogger("app.services.characters.upgrade").setLevel(logging.INFO)   # what the upgrade moved, and why
    _setup(copy)
    return 0 if asyncio.run(run(args.project, args.episodes, corrections, args.after_merges)) else 1


if __name__ == "__main__":
    sys.exit(main())
