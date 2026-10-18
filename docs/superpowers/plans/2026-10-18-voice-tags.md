# Voice names, sections and tags — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every voice age/personality/role tags, list voices in seven age-and-gender sections, give the 62 built-in voices Sino-Khmer names, and let users tag and copy voices — shipped as 0.2.3 with the voice-message player already on `staging`.

**Architecture:** Four nullable columns on `voices` (added at startup for installed apps, plus an alembic migration). The backend derives each voice's section (`VoiceResponse.section`) from its tags, so the rule is tested in pytest and the frontend only maps section keys to labels. Built-in names and tags live in `manifest.json` and are written by the seeder on every start.

**Tech Stack:** FastAPI + SQLAlchemy (async, SQLite/Postgres), pydantic v2, pytest; React + TypeScript + TanStack Query + Tailwind; no frontend test runner (lint + build + browser check).

**Spec:** `docs/superpowers/specs/2026-10-17-voice-tags-design.md`

## Global Constraints

- Built-in ids never change (`builtin-<nn>-<character>-<letter>`); built-in `description` text never changes.
- Names: Sino-Khmer (Chinese-origin family name first, Khmer given name), **Latin letters only**, unique per voice; regex `^[A-Z][a-z]+ [A-Z][a-z]+$`.
- Who: `man`, `woman`, `boy`, `girl`. Age: `child`, `teen`, `20s`, `30s`, `40s`, `50s`, `60s`, `70s+`.
- Sections, in order: Young men, Young women, Older men, Older women, Elders, Teens and children, Narrators. "My voices" (own voices with no section) sits above them and is hidden when empty.
- Tag colours: age grey, personality pink, role blue. The ▶ voice-message player is unchanged.
- Built-in voices can't be renamed, retagged or deleted (403, existing message). Any voice can be copied.
- Changing only tags never regenerates a voice's sample.
- Routes keep `Depends(get_db, scope="function")`. API shape chain: `models.py` + migration → `schemas.py` → route → `types/index.ts` → `api/client.ts` → `hooks/useApi.ts`.
- Never run the app, alembic or a server against `backend/.env`'s database: set `DATABASE_URL` to a temp SQLite file (it wins over `DUBIFY_DATA_DIR`) or `DATABASE_URL=` plus a temp `DUBIFY_DATA_DIR`.
- Before every commit: secret scan of the staged diff (`AIza`, `hf_`, `sk-`, `ghp_`, `github_pat_`, `PRIVATE KEY`, `gradio.live`, `neon.tech`); never stage `backend/.env`, `backend/backups/`, `.claude/settings.local.json`, `vendor/`, `frontend/src-tauri/binaries/`, `frontend/src-tauri/backend/`, or the root `package.json`/`package-lock.json`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Someone updating from 0.2.2 with voices of their own (all four columns NULL): the Voices tab, both pickers and TTS keep working and those voices sit in My voices — Task 1 `test_startup_adds_tag_columns_to_a_0_2_2_database`, Task 2 `test_a_voice_without_tags_has_empty_tags_and_no_section`.
2. Editing only the tags of a design voice must not re-bake it (no TTS call, sample untouched) — Task 2 `test_tag_only_edit_never_rebakes_a_design_voice`.
3. Messy typed tags ("  Warm ", "warm", 7 tags, a 30-character tag) are trimmed and de-duplicated, or refused with 422 — Task 2 `test_tags_are_cleaned_and_limited`.
4. Deleting a copy never touches the original's clip; copying a voice that has no clip works — Task 2 `test_copy_has_its_own_clip_and_deleting_it_keeps_the_original`, `test_copying_a_voice_without_a_clip`.
5. Searching ("cold", "narrator", "Heng") opens every section with a match and hides empty ones; clearing the search restores the sections as they were — Task 6, browser check step.

---

### Task 1: Tag columns, and adding them to installed apps

**Files:**
- Modify: `backend/app/models/models.py:73-103` (Voice)
- Modify: `backend/app/core/database.py:89-106` (init_db)
- Create: `backend/alembic/versions/d7e8f9a0b1c2_add_tags_to_voices.py`
- Test: `backend/app/tests/test_builtin_voices.py` (next to its `_pack` helper)

**Interfaces:**
- Produces: `Voice.gender: str | None` (`String(10)`), `Voice.age_group: str | None` (`String(10)`), `Voice.personality: list[str] | None` (`JSON`, `default=list`), `Voice.roles: list[str] | None` (`JSON`, `default=list`).
- Produces: `async def add_missing_columns(conn: AsyncConnection, table: str, columns: list[tuple[str, str]]) -> list[str]` (returns the names it added), `VOICE_TAG_COLUMNS = [("gender", "VARCHAR(10)"), ("age_group", "VARCHAR(10)"), ("personality", "JSON"), ("roles", "JSON")]`, `async def init_db(bind: AsyncEngine | None = None) -> None` (defaults to the module engine).

- [ ] **Step 1: Write the failing test** `test_startup_adds_tag_columns_to_a_0_2_2_database(tmp_path, monkeypatch)`: file engine `sqlite+aiosqlite:///{tmp_path}/old.db`; create the v0.2.2 `voices` table by raw SQL (`id VARCHAR PRIMARY KEY, name VARCHAR(120) NOT NULL, mode VARCHAR(20), description TEXT, reference_audio_path VARCHAR, reference_transcript TEXT, cfg_value FLOAT, inference_timesteps INTEGER, seed INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP`) and insert `('v1', 'Narrator', 'design', …, seed 5)`; `await core_db.init_db(engine)` **twice** (second start must not fail). Assert:

```python
assert {"gender", "age_group", "personality", "roles"} <= columns          # via inspect(...).get_columns("voices")
v = (await db.execute(select(Voice))).scalar_one()
assert (v.id, v.name, v.seed, v.gender, v.personality) == ("v1", "Narrator", 5, None, None)
monkeypatch.setattr(core_db, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False))
monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
assert await builtin_voices.seed_builtin_voices(_pack(tmp_path)) == 2
```

- [ ] **Step 2: Run it — expect FAIL** (`init_db()` takes no argument / columns missing): `cd backend && venv/bin/python -m pytest app/tests/test_builtin_voices.py -k 0_2_2 -v`

- [ ] **Step 3: Implement.** Columns on `Voice`. `add_missing_columns` reads existing names with `await conn.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns(table)})` and issues `ALTER TABLE {table} ADD COLUMN {name} {type}` only for missing ones (never a failing ALTER — on Postgres one would abort the whole transaction). `init_db(bind)` runs `create_all`, then `add_missing_columns` for the existing `segments` list (same three columns and types as today) and for `voices` with `VOICE_TAG_COLUMNS`, logging what it added.

- [ ] **Step 4: Migration** `d7e8f9a0b1c2`, revises `c6d7e8f9a0b1`, same inspect-then-`op.add_column` style as `c6d7e8f9a0b1_add_title_to_jobs.py`; downgrade drops the four columns. Docstring says why (tags for sections; installed apps get the columns from `init_db`).

- [ ] **Step 5: Run tests — expect PASS**: `venv/bin/python -m pytest app/tests/ -q` → all pass (the known flaky `test_batch_segments_synthesize_concurrently` may fail once; re-run it alone).

- [ ] **Step 6: Check the migration on a throwaway file** (a fresh DB can't be migrated from empty; the chain starts from `create_all`):

```bash
T=$(mktemp -d); export DATABASE_URL="sqlite+aiosqlite:///$T/m.db" DUBIFY_DATA_DIR= GEMINI_API_KEY= HF_TOKEN=
venv/bin/python -c "import asyncio; from app.core.database import engine, Base; import app.models.models
async def m():
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
asyncio.run(m())"
for c in gender age_group personality roles; do sqlite3 $T/m.db "ALTER TABLE voices DROP COLUMN $c"; done
venv/bin/python -m alembic stamp c6d7e8f9a0b1 && venv/bin/python -m alembic upgrade head \
 && venv/bin/python -m alembic downgrade -1 && venv/bin/python -m alembic upgrade head
sqlite3 $T/m.db "select version_num from alembic_version"; rm -rf $T
```
Expected: no errors, final line `d7e8f9a0b1c2`. (Use `python -m alembic`; the `venv/bin/alembic` script has a stale shebang.)

- [ ] **Step 7: Commit** — `feat(voices): tag columns, added at startup for installed apps`

### Task 2: Tags, sections and copies in the voices API

**Files:**
- Create: `backend/app/services/voice_tags.py`
- Modify: `backend/app/schemas/schemas.py:382-425` (VoiceUpdate, VoiceResponse)
- Modify: `backend/app/api/routes/voices.py` (create_voice, update_voice, new copy route)
- Test: `backend/app/tests/test_voice_tags.py`

**Interfaces:**
- Consumes: Task 1 columns.
- Produces (`voice_tags.py`): `GENDERS = ("man", "woman", "boy", "girl")`, `AGE_GROUPS = ("child", "teen", "20s", "30s", "40s", "50s", "60s", "70s+")`, `SECTIONS = ("young_men", "young_women", "older_men", "older_women", "elders", "teens_children", "narrators")`, `MAX_TAGS = 6`, `MAX_TAG_LENGTH = 24`, `def normalize_tags(tags: list[str] | None) -> list[str]` (raises `ValueError` past the limits), `def voice_section(gender: str | None, age_group: str | None, roles: list[str] | None) -> str | None`.
- Produces (schemas): `VoiceTagFields` (`gender: Optional[Literal[…GENDERS]]`, `age_group: Optional[Literal[…AGE_GROUPS]]`, `personality: Optional[List[str]]`, `roles: Optional[List[str]]`, lists run through `normalize_tags`); `VoiceUpdate(VoiceTagFields)`; `VoiceResponse` gains `gender`, `age_group`, `personality: List[str]`, `roles: List[str]` (NULL → `[]`) and computed `section: Optional[str]`.
- Produces (API): `POST /api/v1/voices/` accepts form fields `gender`, `age_group` and repeated `personality` / `roles`; `PATCH /api/v1/voices/{id}` accepts the tag fields, and an explicit `"gender": null` / `"age_group": null` clears them; `POST /api/v1/voices/{id}/copy` → 201 `VoiceResponse`.

`voice_section` rule, first match wins:
1. a role equal to "narrator" (any case) → `narrators`
2. no gender → `None` (My voices)
3. `boy`/`girl`, or age `child`/`teen` → `teens_children`
4. age `60s`/`70s+` → `elders`
5. age `40s`/`50s` → `older_men` (man) / `older_women` (woman)
6. otherwise (20s, 30s or no age) → `young_men` / `young_women`

- [ ] **Step 1: Write the failing tests** in `test_voice_tags.py`:
  - `test_voice_section_rule` — `("man","20s",[])→young_men`, `("woman","30s",[])→young_women`, `("man","50s",[])→older_men`, `("woman","40s",[])→older_women`, `("woman","70s+",[])→elders`, `("man","60s",[])→elders`, `("girl","child",[])→teens_children`, `("boy",None,[])→teens_children`, `("woman","40s",["Narrator"])→narrators`, `(None,None,["narrator"])→narrators`, `(None,"20s",["Lead"])→None`, `("man",None,[])→young_men`.
  - `test_tags_are_cleaned_and_limited` — PATCH an own voice with `personality=["  Warm ", "warm", "Very   gentle"]` → response `["Warm", "Very gentle"]`; 7 distinct tags → 422; a 30-character tag → 422; `gender="robot"` → 422; `age_group="25"` → 422.
  - `test_create_with_tags_and_section` — multipart POST (mode `clone`, a small WAV, `gender=woman`, `age_group=40s`, `personality=Calm`, `personality=Elegant`, `roles=Empress`) → 201 with those values and `section == "older_women"`; listing shows the same.
  - `test_clearing_who_moves_a_voice_back_to_my_voices` — PATCH `{"gender": null}` on a tagged voice → `gender is None`, `section is None`, personality kept.
  - `test_tag_only_edit_never_rebakes_a_design_voice` — monkeypatch `voices._bake_design_voice` to record calls; create a design voice (bake recorded once), PATCH `{"personality": ["Warm"], "gender": "man", "age_group": "20s"}` → still one call.
  - `test_a_voice_without_tags_has_empty_tags_and_no_section` — insert a `Voice` with all four columns NULL via `db_sessionmaker` → listed with `personality == [] and roles == [] and section is None`.
  - `test_built_in_tags_cannot_be_changed` — a `Voice(id="builtin-00-test-a", …)` row inserted with `db_sessionmaker` (the 403 depends only on the id): PATCH `{"roles": ["Villain"]}` → 403.
  - `test_copy_has_its_own_clip_and_deleting_it_keeps_the_original` — the same kind of built-in row, tagged, whose `reference_audio_path` is a small WAV in `tmp_path`: copy → 201, new non-`builtin-` id, `name == "<name> (copy)"`, same mode/transcript/description/cfg/steps/seed/tags, `reference_audio_path` under `voices.VOICES_DIR / <new id>` with identical bytes; DELETE the copy → 204 and the original WAV still exists.
  - `test_copying_a_voice_without_a_clip` — design voice whose bake failed (empty `reference_audio_path`) → copy is 201 with empty `reference_audio_path`; unknown id → 404.

- [ ] **Step 2: Run — expect FAIL**: `venv/bin/python -m pytest app/tests/test_voice_tags.py -v`

- [ ] **Step 3: Implement `voice_tags.py`** — `normalize_tags`: trim, collapse inner whitespace, drop empties, de-duplicate case-insensitively keeping the first spelling, then raise past `MAX_TAGS` or `MAX_TAG_LENGTH`.

- [ ] **Step 4: Implement the schemas** — validators as above; `VoiceResponse.section` is a `computed_field` calling `voice_section`.

- [ ] **Step 5: Implement the routes.**
  - `create_voice`: new params `gender: Optional[str] = Form(None)`, `age_group: Optional[str] = Form(None)`, `personality: List[str] = Form([])`, `roles: List[str] = Form([])`. Validate by building `VoiceTagFields(...)`; on `ValidationError` raise `HTTPException(422, detail=e.errors(include_url=False, include_context=False))`.
  - `update_voice`: tag fields come from `payload.model_dump(include={"gender", "age_group", "personality", "roles"}, exclude_unset=True)`, so an explicit null clears. Tags stay out of `identity_changed`.
  - `copy_voice` (`POST /{voice_id}/copy`, `status_code=201`): 404 when missing; new `Voice` with every field copied, `name=f"{name} (copy)"[:120]`, then the clip copied with `shutil.copyfile` to `VOICES_DIR/<new id>/reference<same suffix>` when the source file exists.

- [ ] **Step 6: Run all backend tests — expect PASS**: `venv/bin/python -m pytest app/tests/ -q`

- [ ] **Step 7: Commit** — `feat(voices): tags, sections and copies in the voices API`

### Task 3: The seeder writes built-in names and tags

**Files:**
- Modify: `backend/app/services/builtin_voices.py:64-100` (`wanted`)
- Test: `backend/app/tests/test_builtin_voices.py`

**Interfaces:**
- Consumes: Task 1 columns; manifest entries may carry `gender`, `age_group`, `personality`, `roles` (missing → `None` / `[]`, so old packs still seed).

- [ ] **Step 1: Write the failing test** `test_seeding_writes_tags_and_a_new_manifest_overwrites_them`: add `gender="woman"`, `age_group="20s"`, `personality=["Bright"]`, `roles=["Lead"]` to one `_pack` entry; seed → the voice has them; edit the manifest (`age_group="30s"`, `name="Ly Sophea"`) → re-seed returns 1 and the voice has the new values; the other pack entry (no tag keys) has `gender is None` and `personality == []`.
- [ ] **Step 2: Run — expect FAIL**: `venv/bin/python -m pytest app/tests/test_builtin_voices.py -v`
- [ ] **Step 3: Implement** — add the four keys to `wanted` (`entry.get("gender")`, `entry.get("age_group")`, `list(entry.get("personality") or [])`, `list(entry.get("roles") or [])`).
- [ ] **Step 4: Run — expect PASS** (same command), then the full suite.
- [ ] **Step 5: Commit** — `feat(voices): built-in voices carry their names and tags`

### Task 4: Draft names and tags for the 62 built-in voices

**Files:**
- Create (outside the repo): `~/Documents/Movelo-Voice-Casting/draft_tags.py`
- Modify (outside the repo): `~/Documents/Movelo-Voice-Casting/make_builtin.py`
- Modify: `backend/app/assets/builtin_voices/manifest.json`
- Test: `backend/app/tests/test_builtin_voices.py::test_the_shipped_library_is_complete`

**Interfaces:**
- Consumes: manifest entry keys from Task 3; `voice_section` from Task 2.

- [ ] **Step 1: Extend the failing test** `test_the_shipped_library_is_complete`: every voice has `gender in GENDERS`, `age_group in AGE_GROUPS`, 1–3 personality tags, 1–2 role tags, a name matching `^[A-Z][a-z]+ [A-Z][a-z]+$`; names unique; `voice_section(...)` is never `None`; all seven sections are used.
- [ ] **Step 2: Run — expect FAIL** (no tags yet).
- [ ] **Step 3: Write `draft_tags.py <manifest>`**, which edits the manifest in place:
  - **Baseline per character** (character key = id without `builtin-` and the take letter):

    | key | who | age | personality | roles |
    |---|---|---|---|---|
    | 01-young-male-lead | man | 20s | Warm, Gentle, Bright | Lead |
    | 02-young-female-lead | woman | 20s | Sweet, Bright, Gentle | Lead |
    | 03-cold-ceo | man | 20s | Cold, Calm, Deep | Hero, CEO |
    | 04-strong-heroine | woman | 20s | Firm, Warm | Heroine |
    | 05-gentle-second-lead | man | 20s | Soft, Kind, Gentle | Second lead |
    | 06-scheming-woman | woman | 20s | Sly, Elegant | Villain, Schemer |
    | 07-villain | man | 40s | Menacing, Cold, Deep | Villain |
    | 08-father | man | 50s | Serious, Warm | Father |
    | 09-mother | woman | 50s | Warm, Gentle, Kind | Mother |
    | 10-grandfather | man | 70s+ | Wise, Kind | Grandparent |
    | 11-grandmother | woman | 70s+ | Soft, Gentle, Warm | Grandparent |
    | 13-girl | girl | child | Sweet, Cheerful, Playful | Child |
    | 14-teen-boy | boy | teen | Lively, Bright | Student |
    | 15-teen-girl | girl | teen | Cheerful, Playful, Bright | Student |
    | 16-comic-sidekick | man | 20s | Funny, Lively | Sidekick |
    | 17-best-friend | woman | 20s | Cheerful, Lively, Warm | Best friend |
    | 18-emperor | man | 40s | Regal, Commanding, Deep | Emperor |
    | 19-empress | woman | 40s | Regal, Elegant, Calm | Empress |
    | 20-general | man | 30s | Rough, Commanding | General, Warrior |
    | 21-shy-servant | woman | 20s | Timid, Soft | Servant, Maid |
    | 22-calm-doctor | woman | 30s | Calm, Reassuring, Kind | Doctor |
    | 23-male-narrator | man | 40s | Deep, Warm, Calm | Narrator |

  - **Age per take (a draft for the user to correct by ear):** median pitch of each take; within a character with 2+ takes, a take ≥ 12% above the character's median moves one age band younger, ≤ 11% below moves one band older. Bands follow `AGE_GROUPS` order, clamped to `20s…70s+` for man/woman and `child…teen` for boy/girl. Pitch (numpy only):

    ```python
    # 40 ms frames, 10 ms hop; frames under 10% of the loudest frame's RMS are unvoiced
    ac = np.correlate(frame, frame, "full")[len(frame) - 1:]
    lag = lo + np.argmax(ac[lo:hi])          # lo = rate // 500, hi = rate // 60
    f0 = rate / lag                           # take = median f0 over voiced frames
    ```

  - **Names:** family names `Heng, Ly, Lim, Tan, Chea, Kim, Seng, Meng, Taing, Hok, Ngov, Leang, Kheang, Chhay, Ung, Hong, Chhun, Kong, Sieng, Yim`. Male given names (man/boy, 33 needed): `Dara, Visal, Rithy, Vannak, Piseth, Rotha, Sovann, Kosal, Samnang, Virak, Narith, Vuthy, Pheakdey, Makara, Sopheak, Ratanak, Panha, Vichet, Sambath, Seyha, Raksa, Thearith, Sophal, Veasna, Sothy, Phirun, Bunthoeun, Chamroeun, Davuth, Sokhom, Vibol, Sereyrath, Borey, Kakada, Sovath`. Female given names (woman/girl, 29 needed): `Sophea, Bopha, Sreymom, Chanthou, Sreyneang, Kanha, Malis, Pisey, Sokunthea, Leakhena, Davy, Raksmey, Nary, Sothea, Channary, Sophorn, Kunthea, Sreyleak, Chenda, Mealea, Sreypov, Thida, Romduol, Boramy, Socheata, Phalla, Vicheka, Rachana, Sreynich, Monika, Sovanny, Vanny`. Each given name is used once; the takes of one character get different family names.
- [ ] **Step 4: Run it** on `backend/app/assets/builtin_voices/manifest.json`; print a table (id, name, who, age, pitch, section) and the section counts (expect roughly 15/17/9/6/6/6/3; age moves may shift a few).
- [ ] **Step 5: Change `make_builtin.py`** so that, when `<out>/manifest.json` exists, entries whose id is already there keep their `name`, `gender`, `age_group`, `personality` and `roles`; new ids get the old-style draft name and no tags. Check: copy the assets folder to a temp dir, run the script into it, `diff` the two manifests → only `duration_s` rounding may differ, never names or tags.
- [ ] **Step 6: Run the backend tests — expect PASS.**
- [ ] **Step 7: Commit** — `feat(voices): Sino-Khmer names and tags for the 62 built-in voices (draft)`

### Task 5: Frontend data layer, tag chips and the voice pickers

**Files:**
- Create: `frontend/src/lib/voiceTags.ts`
- Create: `frontend/src/components/voices/VoiceTags.tsx`
- Modify: `frontend/src/types/index.ts:158-176`, `frontend/src/api/client.ts:315-370`, `frontend/src/hooks/useApi.ts:495-535`, `frontend/src/components/voices/VoiceOptions.tsx`

**Interfaces:**
- Consumes: Task 2 API.
- Produces (types): `VoiceGender`, `VoiceAgeGroup`, `VoiceSectionKey`; `Voice` gains `gender?: VoiceGender | null`, `age_group?: VoiceAgeGroup | null`, `personality?: string[]`, `roles?: string[]`, `section?: VoiceSectionKey | null`; `interface VoiceTagValue { gender: VoiceGender | null; age_group: VoiceAgeGroup | null; personality: string[]; roles: string[] }`.
- Produces (client/hooks): `VoiceCreateInput` gains the four tag fields (form: `gender`/`age_group` only when set; one `personality` / `roles` entry per tag); `voices.update` accepts the tag fields; `voices.copy(id: string): Promise<Voice>`; `useCopyVoice()` (invalidates `['voices']`).
- Produces (`voiceTags.ts`): `SECTIONS: { key: VoiceSectionKey; label: string; hint?: string }[]` (hints: "20s to 30s" for the young sections, "40s to 50s" for the older ones, "60s and up" for Elders); `GENDERS`, `AGE_GROUPS` (`{ value, label }[]`); `PERSONALITY_TAGS = ['Warm','Gentle','Bright','Sweet','Lively','Cold','Calm','Deep','Firm','Soft','Kind','Sly','Elegant','Menacing','Serious','Wise','Cheerful','Playful','Funny','Regal','Commanding','Rough','Timid','Reassuring']`; `ROLE_TAGS = ['Lead','Second lead','Hero','Heroine','Villain','Schemer','Sidekick','Best friend','Father','Mother','Grandparent','Emperor','Empress','General','Warrior','Servant','Maid','Doctor','CEO','Student','Child','Narrator']`; `MAX_TAGS = 6`; `ageLabel(v): string | null` ("Man, 20s", "Girl, child", "Man" when no age); `voiceMatches(v: Voice, query: string): boolean` (name, description, tags, age label, section label; case-insensitive); `groupVoices(voices: Voice[]): { key: 'mine' | VoiceSectionKey; label: string; hint?: string; voices: Voice[] }[]` — "My voices" first (own voices with no section, newest first), then `SECTIONS` order; within a section own voices first (newest first), then built-in by age order, then name; empty groups dropped; `pickerLabel(v: Voice): string` ("Heng Dara — Man, 20s, warm, gentle": the age label plus the first two personality tags in lower case; an own voice inside a section gets " (yours)" after its name).
- Produces (`VoiceTags.tsx`): `VoiceTags({ voice }: { voice: Voice })` — grey age chip (`bg-white/10 text-white/70`), pink personality chips (`bg-brand-500/15 text-brand-300`), blue role chips (`bg-blue-500/10 text-blue-400`, as `ui/Badge` "blue"); renders nothing when there are no tags.

- [ ] **Step 1: Implement** types → client → hooks → `voiceTags.ts` → `VoiceTags.tsx`.
- [ ] **Step 2: `VoiceOptions`** renders one `<optgroup>` per `groupVoices` group, each `<option>` showing `pickerLabel(v)`.
- [ ] **Step 3: Verify**: `cd frontend && npm run lint && npm run build` → no errors.
- [ ] **Step 4: Commit** — `feat(voices): voice tags in the data layer and the voice pickers`

### Task 6: Voices tab — sections, tag editing and Make a copy

**Files:**
- Create: `frontend/src/components/voices/VoiceSection.tsx`, `frontend/src/components/voices/VoiceTagEditor.tsx`
- Modify: `frontend/src/components/panels/VoicesPanel.tsx`

**Interfaces:**
- Consumes: Task 5 (`groupVoices`, `voiceMatches`, `VoiceTags`, `useCopyVoice`, tag fields on `useCreateVoice` / `useUpdateVoice`).
- Produces: `VoiceSection({ label, hint, count, open, onToggle, children })` — a header button (chevron, label, hint, count, `aria-expanded`) over a collapsible body. `VoiceTagEditor({ value, onChange, idPrefix }: { value: VoiceTagValue; onChange: (v: VoiceTagValue) => void; idPrefix: string })` — Who (4 toggle buttons; clicking the selected one clears it), Age (8 toggle buttons), Personality and Role (toggle chips from the lists, `aria-pressed`, at most `MAX_TAGS` each, plus an "Add your own" input that adds on Enter: trimmed, ≤ 24 characters, no case-insensitive duplicate).

- [ ] **Step 1: Sections.** Replace `myVisible` / `builtinGroups` with `groupVoices(visibleVoices)`, where `visibleVoices` filters by mode and by `voiceMatches`. `openSections: Set<string>`, starting with only `'mine'` open. While the search box has text, every shown section is open; clearing the search restores `openSections`.
- [ ] **Step 2: Rows.** Built-in row: name, `<VoiceTags>`, then `player(v, 32)`, Make a copy (`CopyPlus` icon, aria-label `Make a copy of ${name}`, tooltip "Make a copy you can edit") and the existing Copy voice ID button. Built-in descriptions are not shown. Own voice card: the existing card plus `<VoiceTags>` under the name row, a "Yours" badge beside the mode badge, and two new actions — Tags (`Tag` icon, aria-label `Edit tags of ${name}`) opening an inline `<VoiceTagEditor>` with Save/Cancel (Save disabled while a "who" has no age; saves via `updateVoice` with the four fields), and Make a copy.
- [ ] **Step 3: Create form.** `<VoiceTagEditor>` after the description field; new blocker "Choose an age for this voice" when a who is set without an age; `handleSave` sends the tags; `resetForm` clears them.
- [ ] **Step 4: Make a copy.** `handleCopy(v)`: `copyVoice.mutateAsync(v.id)`, then toast `Copied as “${copy.name}” — it's in ${section label or 'My voices'}`; errors toast "Couldn't copy this voice".
- [ ] **Step 5: Verify**: `npm run lint && npm run build` → no errors.
- [ ] **Step 6: Browser check** against a throwaway backend (`DATABASE_URL= DUBIFY_DATA_DIR=<temp>`, keys blank except the Hugging Face voice Space, which is on by default) and `npm run dev`, in dark and light themes. Check:
  - all seven sections show the expected counts and open and close;
  - each section's rows show tags in the right colours and ▶ plays;
  - search behaves as in Review Focus 5;
  - creating a voice with who, age and tags puts it in the right section;
  - editing tags moves it, and removing its who moves it back to My voices;
  - Make a copy on a built-in voice shows "(copy)" beside the original with "Yours";
  - deleting the copy leaves the original playing;
  - the Speakers and transcript voice pickers show the same groups.
- [ ] **Step 7: Commit** — `feat(voices): Voices tab in sections, with tags and Make a copy`

### Task 7: The user checks the 62 voices (checkpoint)

- [ ] **Step 1:** Start the preview from Task 6, step 6, and tell the user how to listen: open each section and play every voice.
- [ ] **Step 2:** For each correction they send (name, who, age, tags), edit `manifest.json`, restart the backend (the seeder applies it), and confirm. Re-run `test_builtin_voices.py`.
- [ ] **Step 3:** Once the user says the voices are right, commit — `fix(voices): built-in names and tags as checked by ear`. Nothing is released before this step.

### Task 8: Docs and release 0.2.3

**Files:** `CLAUDE.md`, `website/studio.html`, `frontend/src-tauri/tauri.conf.json`, `frontend/src-tauri/Cargo.toml`, `frontend/src-tauri/Cargo.lock`, `frontend/package.json`, `frontend/package-lock.json`

- [ ] **Step 1: Docs.** In `CLAUDE.md`'s built-in voices sentence, add that names and tags come from `manifest.json` and that sections are derived by `voice_tags.voice_section`. Add a Gotcha: installed apps never run alembic, so a column on an existing table also needs an entry in `init_db`'s `add_missing_columns` lists. In `website/studio.html`, update the voices line to say the voices are sorted by age and gender with tags (describe only what the app does).
- [ ] **Step 2: Version 0.2.2 → 0.2.3** in the five version files (the package's own entry in `Cargo.lock`; the top two `version` fields in `package-lock.json`).
- [ ] **Step 3: Full check**: backend tests, `npm run lint`, `npm run build`. Commit — `chore: release 0.2.3`. Push `staging`; open the PR staging → main titled "Staging".
- [ ] **Step 4: Tag and build**: `git tag v0.2.3 <staging HEAD> && git push origin v0.2.3`; wait for both chips of `desktop-release.yml` to finish (until-loop on `gh run view`), giving the user the expected wait.
- [ ] **Step 5: Verify the draft** in the scratchpad:
  - Download with `gh release download v0.2.3`.
  - The assets are `Movelo-Studio-Apple-Silicon.dmg`, `Movelo-Studio-Intel.dmg`, both `.app.tar.gz` + `.sig`, and `latest.json`.
  - `latest.json` says 0.2.3; its URLs and signatures match the files.
  - Every `.sig` verifies against `plugins.updater.pubkey` in `tauri.conf.json`, using `/usr/bin/python3` (it has `cryptography`):

    ```python
    pk  = base64.b64decode(base64.b64decode(pubkey).decode().splitlines()[1])      # 2 alg, 8 key id, 32 key
    sig_lines = base64.b64decode(open(sig_file).read()).decode().splitlines()
    sig = base64.b64decode(sig_lines[1])                                          # 2 alg, 8 key id, 64 sig
    msg = hashlib.blake2b(data).digest() if sig[:2] == b"ED" else data
    assert sig[2:10] == pk[2:10]; key = Ed25519PublicKey.from_public_bytes(pk[10:])
    key.verify(sig[10:], msg)
    key.verify(base64.b64decode(sig_lines[3]), sig[10:] + sig_lines[2].split(":", 1)[1].strip().encode())
    ```

  - Mount each DMG. `Contents/Resources/backend/movelo-backend --self-test` prints `built-in voices: 62` (Intel under `arch -x86_64`).
  - Upgrade check, with one throwaway `HOME` for both apps:
    - first launch 0.2.2 (from the v0.2.2 release DMG), create one clone voice through the API with a small WAV, then quit;
    - then launch 0.2.3 and check:
      - the startup log shows the four columns being added;
      - that voice is listed with `section: null`;
      - all 62 built-in voices have a section.
  - Delete every downloaded and temporary copy afterwards.
- [ ] **Step 6: Publish** — after confirming with the user — with notes (sections and tags, Sino-Khmer names, Make a copy, the voice-message player). Check that `releases/latest/download/latest.json` serves 0.2.3.
- [ ] **Step 7: Merge** the PR (merge commit), check that the website deploy finished and that the Studio page shows the new text. Stop the preview servers and delete their temp folders.
