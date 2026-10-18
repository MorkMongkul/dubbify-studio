# Voice names, sections and tags — design

Date: 2026-10-17 · Status: draft for approval · Ships in: 0.2.3 (with the voice-message player already on `staging`)

## Why

While dubbing, the user looks at an actor on screen and has to find a fitting voice among 62 built-in voices plus their own. Today the built-in voices are named by role plus a take letter ("Cold CEO / hero B"), grouped by that role, and nothing says how old a take sounds — although the user kept several takes of a character precisely because they sound like different ages. They pick a voice by what they can see (man or woman, roughly how old) and by the kind of character (cold, gentle, villain, emperor…). The library should be laid out the same way.

## Goals

1. Every voice shows small **tags**: age (grey), personality (pink), kind of role (blue).
2. The Voices tab lists voices in **sections by age and gender** (the user picked this over filter chips and over a compact list).
3. Built-in voices get **person names**: Sino-Khmer names (a Chinese-origin family name, then a Khmer given name, e.g. *Heng Dara*, *Ly Sophea*, *Kim Vannak*), **Latin letters only**, one unique name per take.
4. The user can give **their own voices** the same tags, so they sit in the right section next to the built-in ones.

## Non-goals

- Filter chips (option B) — rejected.
- Khmer-script names — the user chose Latin letters only.
- New voices or new takes; any change to how voices sound.
- Suggesting a voice automatically from the actor.

## Anti-goals (a failure even if it works)

- An actor's voice changes, or a speaker loses its voice, because a built-in voice was renamed. Built-in ids stay exactly as they are (`builtin-<nn>-<character>-<letter>`).
- One of the user's voices disappears or lands in a section it doesn't belong to without them choosing it.
- Tags that contradict what the voice sounds like (a take tagged "20s" that sounds 40). The user checks every built-in voice's age by ear before release.

## What the user decided

### Sections (Voices tab)

Seven sections, in this order, each with a count; each opens and closes:

| Section | Who goes in it |
|---|---|
| Young men | man, 20s–30s |
| Young women | woman, 20s–30s |
| Older men | man, 40s–50s |
| Older women | woman, 40s–50s |
| Elders | man or woman, 60s and up |
| Teens and children | boy or girl |
| Narrators | any voice with the role *Narrator* (this wins over the rules above) |

Current built-in split: 15 / 17 / 9 / 6 / 6 / 6 / 3.

Above the sections, **My voices** holds the user's own voices that have no "who" yet (including every voice they already have today). It is hidden when empty.

### Each voice

Name on top (e.g. *Heng Dara*), then tags, then the existing voice-message player (round ▶, waveform, length — unchanged).

- Grey age tag: "Man, 20s", "Woman, 50s", "Girl, child", "Boy, teen".
- Pink personality tags (usually 2–3).
- Blue role tags (usually 1–2).
- The user's own voices keep their existing actions (rename, re-roll, delete) and get a small mark showing they're theirs.

### Own voices

- Creating or editing a voice: choose **who** (man, woman, boy, girl), **age** (child, teen, 20s, 30s, 40s, 50s, 60s, 70s+), then pick personality and role tags from the same list the built-in voices use, with a way to type a new tag.
- Once a voice has a "who", it moves into its section; without one it stays in My voices.
- Built-in voices can't be renamed, retagged or deleted (as today: 403). **Make a copy** on any voice creates an own voice with the same reference clip, transcript, settings and tags, which the user can then change.

### Names and ages of the built-in voices

- I draft all 62 names and tags. Age comes from the character's description, refined per take from how high or low it sounds (median pitch), since the takes differ in age and nothing records which is which.
- The user reviews the draft in a preview of the app (a throwaway data folder, as before) and corrects names, ages and tags in chat. Nothing ships until they've done this.
- *(My call)* Built-in voices' `description` text stays exactly as it is. The Gemini backup voice is picked from words in it (`tts_client._pick_gemini_voice`), so rewriting it per take would change how a voice sounds when VoxCPM is down. In the new layout built-in rows show only name, tags and player (as in the approved mockup), so a description saying "late twenties" on a take tagged 40s is never seen. Search matches name, tags and description.
- *(My call)* The role vocabulary covers the old characters well enough to find them again (e.g. CEO, schemer, warrior, maid, best friend, sidekick); the old names aren't kept anywhere else.
- *(My call)* From now on `manifest.json` in the repo is the source of truth for built-in names and tags. `~/Documents/Movelo-Voice-Casting/make_builtin.py` (outside the repo) is changed to keep the name and tags of every id already in the manifest when it is re-run, and to write draft fields only for new ids.

## Constraints

- `Voice` is an existing table. **Installed apps never run alembic**: the desktop backend only runs `create_all` plus the idempotent `ALTER TABLE segments …` list in `app/core/database.py:init_db`, and every release so far already shipped with the newest migration. This is the first column added to an existing table for people who already have the app; if their `dubify.db` lacks the columns, every `select(Voice)` fails, which breaks the Voices tab, both pickers, TTS for any line with a voice and (silently) built-in seeding.
  - *(My call)* Add the new `voices` columns to `init_db`'s idempotent `ALTER TABLE` list (same pattern as `segments`), **and** write the alembic migration (head `c6d7e8f9a0b1`) for dev and Postgres.
  - A test starts from a `voices` table shaped like v0.2.2's (no new columns, existing rows) and checks that startup adds the columns, keeps the rows, and seeding then succeeds.
- Works on SQLite (dev, desktop) and Postgres.
- API shape changes follow the repo's chain: `models.py` + migration → `schemas.py` → `routes/voices.py` → `types/index.ts` → `api/client.ts` → `hooks/useApi.ts`.
- Built-in voice data lives in `backend/app/assets/builtin_voices/manifest.json`; `builtin_voices.seed_builtin_voices()` already upserts name/description on every start, so it must also overwrite the built-in voices' tags (an app update can correct a tag).
- Routes keep `Depends(get_db, scope="function")`.
- The voice pickers (the `<select>`s in Speakers and in the transcript, `VoiceOptions.tsx`) are native selects, so sections become `<optgroup>`s.

## Left to the builder

- Storage: e.g. `gender`, `age_group` and two JSON string lists (`personality`, `roles`); naming and types are the builder's call, as long as old rows (no tags) keep working.
- The starting tag vocabulary, drawn from the 21 characters' descriptions (e.g. warm, gentle, bright, sweet, cold, calm, firm, soft, kind, sly, elegant, menacing, wise, cheerful, funny, regal, commanding, rough, timid, reassuring, deep; roles: lead, second lead, hero, heroine, villain, sidekick, best friend, father, mother, grandparent, emperor, empress, general, servant, doctor, CEO, narrator). Keep it short enough to scan.
- Order within a section, whether sections start open or closed, and how search opens the sections that match.
- How the picker option reads (e.g. "Heng Dara — Man, 20s, warm, gentle") and how an own voice is marked there.
- How tags are edited on an existing own voice (inline on its card or in the create form).
- How pitch is measured (no librosa in the venv; numpy is enough).
- Tests: tags round-trip through create/update/list, built-in tags overwrite on seeding, 403 on built-in edits, the copy endpoint, migration up/down; `npm run lint` and `npm run build`; a browser check of the tab and both pickers.
