"""
app/services/story_notes.py
A project's story notes: the user's intro plus what the AI learned from the
episodes the user marked finished (app/services/story_learning.py). One JSON
document per project (Project.story_notes); NULL means empty notes.

Every character, word, episode summary and the tone records who wrote it,
"you" or "ai". Learning (merge_learned) adds new items and updates its own,
but never changes or removes what the user wrote or corrected, and never
brings back an item the user removed ("dismissed"). The user's edits
(apply_user_edit) always win. The rule lives here, not in a prompt.

The notes reach the AI as text: for_translation goes into the translation
prompt and stays bounded however many episodes are learned (only the few
episodes before the one being translated); for_assistant goes into the
Assistant's system instruction.

Pure functions only: each takes the notes as stored (or None) and returns a
new document, never changing its input.
"""
import uuid

MAX_CHARACTERS = 60
MAX_WORDS = 150
MAX_SUMMARY = 400
MAX_TONE = 600
MAX_INTRO = 4000
TRANSLATION_SUMMARIES = 5   # earlier episodes the translation prompt hears about

YOU, AI = "you", "ai"

_LIMITS = {"id": 40, "zh": 60, "km": 120, "about": 300, "note": 200, "speaker": 50}
_CHARACTER_FIELDS = ("zh", "km", "about", "speaker")
_WORD_FIELDS = ("zh", "km", "note")


def empty_notes() -> dict:
    return {
        "intro": "",
        "tone": {"text": "", "by": AI},
        "characters": [],
        "words": [],
        "episodes": {},          # job id → {"summary", "by"}
        "learned": [],           # job ids already read
        "dismissed": {"characters": [], "words": []},   # Chinese names/terms the user removed
    }


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _by(value) -> str:
    return YOU if value == YOU else AI


def _item(raw, fields: tuple[str, ...], prefix: str) -> dict | None:
    """A character or word as stored, or None when it has no Chinese name/term."""
    if not isinstance(raw, dict):
        return None
    item = {field: _text(raw.get(field), _LIMITS[field]) for field in fields}
    if not item["zh"]:
        return None
    return {"id": _text(raw.get("id"), _LIMITS["id"]) or f"{prefix}_{uuid.uuid4().hex[:8]}", **item, "by": _by(raw.get("by"))}


def _items(raw, fields: tuple[str, ...], prefix: str, limit: int) -> list[dict]:
    items, seen = [], set()
    for item in (_item(r, fields, prefix) for r in _list(raw)):
        if item and item["zh"] not in seen and len(items) < limit:
            seen.add(item["zh"])
            items.append(item)
    return items


def normalize(raw) -> dict:
    """The notes as stored, with defaults filled in and limits applied; never raises."""
    notes = empty_notes()
    if not isinstance(raw, dict):
        return notes
    tone = _dict(raw.get("tone"))
    dismissed = _dict(raw.get("dismissed"))
    notes["intro"] = _text(raw.get("intro"), MAX_INTRO)
    notes["tone"] = {"text": _text(tone.get("text"), MAX_TONE), "by": _by(tone.get("by"))}
    notes["characters"] = _items(raw.get("characters"), _CHARACTER_FIELDS, "c", MAX_CHARACTERS)
    notes["words"] = _items(raw.get("words"), _WORD_FIELDS, "w", MAX_WORDS)
    for job_id, episode in _dict(raw.get("episodes")).items():
        summary = _text(_dict(episode).get("summary"), MAX_SUMMARY)
        if summary:
            notes["episodes"][str(job_id)] = {"summary": summary, "by": _by(episode.get("by"))}
    notes["learned"] = list(dict.fromkeys(j for j in _list(raw.get("learned")) if isinstance(j, str) and j))
    for kind in ("characters", "words"):
        names = (_text(z, _LIMITS["zh"]) for z in _list(dismissed.get(kind)))
        notes["dismissed"][kind] = list(dict.fromkeys(z for z in names if z))
    return notes


def with_intro(notes, intro: str) -> dict:
    out = normalize(notes)
    out["intro"] = _text(intro, MAX_INTRO)
    return out


# ── What the AI learned ───────────────────────────────────────────────────────

def _learn_items(items: list[dict], learned, fields: tuple[str, ...], prefix: str,
                 limit: int, dismissed: list[str]) -> list[dict]:
    items = [dict(i) for i in items]
    by_zh = {i["zh"]: i for i in items}
    for raw in _list(learned):
        new = _item({**raw, "id": "", "by": AI}, fields, prefix) if isinstance(raw, dict) else None
        if not new or new["zh"] in dismissed:
            continue
        old = by_zh.get(new["zh"])
        if old is None:
            if len(items) < limit:
                items.append(new)
                by_zh[new["zh"]] = new
        elif old["by"] == AI:
            for field in fields[1:]:
                if new[field]:            # an empty answer never wipes what it had
                    old[field] = new[field]
    return items


def merge_learned(notes, learned: dict, job_ids: list[str]) -> dict:
    """
    Add what was learned from the episodes `job_ids`. `learned` holds `tone`,
    `characters` [{zh, km, about, speaker}], `words` [{zh, km, note}] and
    `summaries` {job id: text}. The user's items are never touched.
    """
    out = normalize(notes)
    learned = _dict(learned)
    tone = _text(learned.get("tone"), MAX_TONE)
    if tone and out["tone"]["by"] == AI:
        out["tone"] = {"text": tone, "by": AI}
    out["characters"] = _learn_items(out["characters"], learned.get("characters"), _CHARACTER_FIELDS, "c",
                                     MAX_CHARACTERS, out["dismissed"]["characters"])
    out["words"] = _learn_items(out["words"], learned.get("words"), _WORD_FIELDS, "w",
                                MAX_WORDS, out["dismissed"]["words"])
    for job_id, summary in _dict(learned.get("summaries")).items():
        text = _text(summary, MAX_SUMMARY)
        if text and out["episodes"].get(job_id, {}).get("by") != YOU:
            out["episodes"][job_id] = {"summary": text, "by": AI}
    out["learned"] = list(dict.fromkeys([*out["learned"], *job_ids]))
    return out


# ── The user's edits ──────────────────────────────────────────────────────────

def _upsert(items: list[dict], raw: dict, fields: tuple[str, ...], prefix: str,
            limit: int, dismissed: list[str]) -> tuple[list[dict], list[str]]:
    """
    One row as the user saved it — only that row changes, so a panel that
    hasn't seen what learning added since can't undo it. A changed or new row
    becomes theirs; one saved as it was keeps its author. Adding a name that's
    already there changes that row.
    """
    sent = {field: raw[field] for field in fields if raw.get(field) is not None}
    old = next((i for i in items if raw.get("id") and i["id"] == raw["id"]), None)
    item = _item({**(old or {}), **sent, "by": YOU}, fields, prefix)
    if item is None:
        return items, dismissed
    target = old or next((i for i in items if i["zh"] == item["zh"]), None)
    if target is None:
        if len(items) >= limit:
            return items, dismissed
        out = [*items, item]
    else:
        item["id"] = target["id"]
        if all(item[field] == target[field] for field in fields):
            item["by"] = target["by"]
        out = [item if i is target else i for i in items if i is target or i["zh"] != item["zh"]]
    return out, [z for z in dismissed if z != item["zh"]]


def _remove(items: list[dict], item_id: str, dismissed: list[str]) -> tuple[list[dict], list[str]]:
    """Take a row out and remember its name, so learning won't bring it back."""
    gone = next((i for i in items if i["id"] == item_id), None)
    if gone is None:
        return items, dismissed
    return [i for i in items if i is not gone], list(dict.fromkeys([*dismissed, gone["zh"]]))


def apply_user_edit(notes, edit: dict) -> dict:
    """
    Apply what the user changed. Every key is optional: `intro`, `tone`,
    `character` / `word` (one row; with its `id` it changes that row, without
    it adds one), `remove_character` / `remove_word` (a row's id) and
    `summaries` {job id: text} (an empty text removes it).
    """
    out = normalize(notes)
    edit = _dict(edit)
    if isinstance(edit.get("intro"), str):
        out["intro"] = _text(edit["intro"], MAX_INTRO)
    if isinstance(edit.get("tone"), str):
        out["tone"] = {"text": _text(edit["tone"], MAX_TONE), "by": YOU}
    for kind, fields, prefix, limit in (("character", _CHARACTER_FIELDS, "c", MAX_CHARACTERS),
                                        ("word", _WORD_FIELDS, "w", MAX_WORDS)):
        key = f"{kind}s"
        if isinstance(edit.get(kind), dict):
            out[key], out["dismissed"][key] = _upsert(out[key], edit[kind], fields, prefix, limit, out["dismissed"][key])
        if isinstance(edit.get(f"remove_{kind}"), str):
            out[key], out["dismissed"][key] = _remove(out[key], edit[f"remove_{kind}"], out["dismissed"][key])
    for job_id, summary in _dict(edit.get("summaries")).items():
        text = _text(summary, MAX_SUMMARY)
        if text:
            out["episodes"][str(job_id)] = {"summary": text, "by": YOU}
        else:
            out["episodes"].pop(str(job_id), None)
    return out


# ── The notes as the AI reads them ────────────────────────────────────────────

def _block(heading: str, notes: dict, episodes: list[tuple[int, str]], marks: bool) -> str:
    def mark(item: dict) -> str:
        return " (you)" if marks and item.get("by") == YOU else ""

    parts = []
    if notes["intro"]:
        parts.append(f"Intro: {notes['intro']}")
    if notes["tone"]["text"]:
        parts.append(f"Tone: {notes['tone']['text']}{mark(notes['tone'])}")
    if notes["characters"]:
        parts.append("Characters:\n" + "\n".join(
            f"- {c['zh']}" + (f" → {c['km']}" if c["km"] else "") + (f" ({c['speaker']})" if c["speaker"] else "")
            + (f": {c['about']}" if c["about"] else "") + mark(c)
            for c in notes["characters"]))
    if notes["words"]:
        parts.append("Words to keep:\n" + "\n".join(
            f"- {w['zh']}" + (f" → {w['km']}" if w["km"] else "") + (f" — {w['note']}" if w["note"] else "") + mark(w)
            for w in notes["words"]))
    if episodes:
        parts.append("Story so far:\n" + "\n".join(
            f"- Episode {number}: {notes['episodes'][job_id]['summary']}{mark(notes['episodes'][job_id])}"
            for number, job_id in episodes))
    return heading + "\n\n" + "\n\n".join(parts) if parts else ""


def for_translation(notes, job_id: str, episode_order: list[str]) -> str:
    """The block for translating episode `job_id`: everything but the story, which is only the few episodes before it."""
    notes = normalize(notes)
    end = episode_order.index(job_id) if job_id in episode_order else len(episode_order)
    earlier = [(n, j) for n, j in enumerate(episode_order[:end], start=1) if j in notes["episodes"]]
    return _block(
        "ABOUT THIS DRAMA — from the user and from episodes already checked. "
        "Keep these names, genders and forms of address.",
        notes, earlier[-TRANSLATION_SUMMARIES:], marks=False)


def for_assistant(notes, episode_order: list[str], *, marks: bool = False) -> str:
    """Everything, every episode's summary included; `marks` tags what the user wrote with "(you)"."""
    notes = normalize(notes)
    episodes = [(n, j) for n, j in enumerate(episode_order, start=1) if j in notes["episodes"]]
    return _block("STORY NOTES FOR THIS PROJECT — from the user and from finished episodes.", notes, episodes, marks)
