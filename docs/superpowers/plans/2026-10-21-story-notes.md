# Story notes, "this line" and chat history — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every project Story notes (an optional intro plus what the AI learns from episodes marked finished) that feed translation and the Assistant; let the Assistant know the line the text cursor is in; and replace the one endless chat per project with chat threads and a history list.

**Architecture:** Three phases, each shippable and committed on its own. Notes are one JSON document per project (`projects.story_notes`) handled by a pure module (`story_notes.py`: merge, edits, prompt blocks, all unit-tested) and filled by a background learning run (`story_learning.py`) that calls Gemini with structured output. Chat gains a `chat_threads` table, `chat_messages.thread_id` and `chat_messages.line_ref`.

**Tech Stack:** FastAPI + SQLAlchemy async (SQLite/Postgres), pydantic v2, pytest + pytest-asyncio; React 19 + TypeScript + TanStack Query 5 + Zustand + Tailwind 3; `node --test` for pure frontend logic.

**Spec:** `docs/superpowers/specs/2026-10-21-story-notes-design.md`

## Global Constraints

- Notes are per project; nothing reads another project's notes. Missing/NULL `story_notes` = empty notes.
- Ownership: every character, word, episode summary and the tone carries `by: "you" | "ai"`; learning never changes or removes a `you` item and never re-adds an item the user removed (`dismissed`). Enforced in `merge_learned`, not by the prompt.
- Limits: 60 characters, 150 words, 400 chars per summary, 600 chars tone, 4,000 chars intro; translation block gets the summaries of at most 5 earlier episodes.
- Learning uses `gemini_models.generate_content("chat", …)`; it runs in the background, one run per project, commits before every Gemini await, and its progress shows only in the Story panel.
- New columns: an alembic migration (inspect-first, like `c6d7e8f9a0b1`) **and** an entry in `init_db`'s `add_missing_columns` list (`backend/app/core/database.py:~125`). New tables need only the model (`create_all`). Migration ids: `f9a0b1c2d3e4` (down `e8f9a0b1c2d3`), `0a1b2c3d4e5f`, `1b2c3d4e5f6a`.
- Routes use `Depends(get_db, scope="function")`; background functions take ids and open `AsyncSessionLocal()` themselves.
- API chain: models + migration → `schemas.py` → route → `types/index.ts` → `api/client.ts` → `hooks/useApi.ts`.
- UI: "less text" (short labels, explanations in tooltips); rail tabs never navigate; Story tab sits before Assistant with the `BookOpen` icon; no ✨ suggestion at the line.
- Tests patch AI at the boundary: `story_learning._ask_gemini`, `chat_agent._call_gemini`, `translator._post_translation`. `conftest.py` blanks keys; set `settings.GEMINI_API_KEY` with monkeypatch where a key is needed.
- Never run the app/alembic against `backend/.env`'s DB. Secret-scan staged diffs; never stage `backend/.env`, `backend/backups/`, `.claude/settings.local.json`, `vendor/`, `frontend/src-tauri/binaries/`, `frontend/src-tauri/backend/`, root `package.json`/`package-lock.json`. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Commands: backend tests `cd backend && venv/bin/python -m pytest app/tests/<file> -v`; full `venv/bin/python -m pytest app/tests -q`; frontend `cd frontend && npx tsc -b && npm run lint && npm test && npm run build`.

## Review Focus

1. A database from 0.2.5 (no new columns, `story_notes` NULL) — Story tab, translation and the Assistant all work and notes read as empty → Task 1 `test_startup_adds_story_columns`, Task 2 `test_missing_notes_read_as_empty`.
2. The user edits a character while learning runs — the edit survives (merge re-reads the latest notes under a row lock) → Task 3 `test_an_edit_made_while_learning_survives`.
3. Learning fails (Gemini error, bad JSON, no key) — notes keep what earlier batches added, the panel says what failed, translation still works → Task 3 `test_a_failed_run_keeps_the_notes_and_says_why`, `test_learning_needs_a_key`.
4. A long project (80 learned episodes, 60 characters) — the translation block stays bounded → Task 2 `test_translation_block_is_bounded`.
5. A character the user deleted comes back in the next learning run → Task 2 `test_a_removed_item_is_not_learned_again`.

---

## Phase 1 — Story notes

### Task 1: Columns for notes and finished episodes

**Files:**
- Modify: `backend/app/models/models.py` (Project L114-136, Job L140-177)
- Modify: `backend/app/core/database.py` (`init_db` list)
- Create: `backend/alembic/versions/f9a0b1c2d3e4_add_story_notes_and_finished_at.py`
- Modify: `backend/app/schemas/schemas.py` (`JobResponse`, `ProjectCreate`)
- Test: `backend/app/tests/test_story_learning.py` (new file; later tasks add to it)

**Interfaces:**
- Produces: `Project.story_notes` (JSON, nullable), `Job.finished_at` (DateTime, nullable), `JobResponse.finished_at: Optional[datetime] = None`, `ProjectCreate.story_intro: str = Field("", max_length=4000)`.

- [ ] **Step 1: Write the failing test** `test_startup_adds_story_columns(tmp_path)`: create an old-shape SQLite file with `projects` and `jobs` tables lacking the columns (copy the pattern of `test_auto_voice.py::test_startup_adds_voice_auto_to_existing_speakers`), run `core_db.init_db(engine)`, assert `"story_notes"` in projects' columns and `"finished_at"` in jobs'.
- [ ] **Step 2: Run it** — Expected: FAIL (columns missing).
- [ ] **Step 3: Implement** the two columns, the `init_db` entries `("projects", [("story_notes", "JSON")])` and `("jobs", [("finished_at", "TIMESTAMP" if postgresql else "DATETIME")])`, the migration (inspect-first add, drop on downgrade), and the two schema fields. `create_project` (projects.py L24-31) must pop `story_intro` before `Project(**data)` and store `story_notes.with_intro(None, intro)` when it's non-blank (Task 2 provides `with_intro`; until then keep the pop and leave notes None).
- [ ] **Step 4: Run** `pytest app/tests/test_story_learning.py -v` → PASS; full backend suite → no new failures.
- [ ] **Step 5: Commit** `feat(story): columns for story notes and finished episodes`.

### Task 2: The notes module (pure)

**Files:**
- Create: `backend/app/services/story_notes.py`
- Test: `backend/app/tests/test_story_notes.py`

**Interfaces:**
- Produces (all pure, no DB):
  - Note shape: `{"intro": str, "tone": {"text": str, "by": "you"|"ai"}, "characters": [{"id", "zh", "km", "about", "speaker", "by"}], "words": [{"id", "zh", "km", "note", "by"}], "episodes": {job_id: {"summary": str, "by"}}, "learned": [job_id], "dismissed": {"characters": [zh], "words": [zh]}}`
  - `empty_notes() -> dict`; `normalize(raw: dict | None) -> dict` (fills defaults, drops malformed items, applies limits, never raises)
  - `with_intro(notes: dict | None, intro: str) -> dict`
  - `merge_learned(notes: dict, learned: dict, job_ids: list[str]) -> dict` — `learned = {"tone": str, "characters": [{zh, km, about, speaker}], "words": [{zh, km, note}], "summaries": {job_id: str}}`
  - `apply_user_edit(notes: dict, edit: dict) -> dict` — `edit` keys all optional: `intro: str`, `tone: str`, `characters: list[{id?, zh, km, about, speaker?}]`, `words: list[{id?, zh, km, note}]`, `summaries: {job_id: str}`
  - `for_translation(notes: dict, job_id: str, episode_order: list[str]) -> str` and `for_assistant(notes: dict, episode_order: list[str]) -> str` — `""` when the notes say nothing
- Matching rules: characters by stripped `zh`, words by stripped `zh`. Updating an `ai` item only overwrites a field when the learned value is non-empty. New items get `id = "c_" / "w_" + uuid4().hex[:8]` and `by: "ai"`. In `apply_user_edit`, an item whose fields changed, or a new item, becomes `you`; an unchanged item keeps its `by`; a stored item missing from the list is removed and its `zh` added to `dismissed`; adding a `zh` back removes it from `dismissed`.

- [ ] **Step 1: Write the failing tests:**
  - `test_missing_notes_read_as_empty`: `normalize(None) == empty_notes()`; `normalize({"characters": [{"zh": ""}, "junk"]})["characters"] == []`.
  - `test_learning_adds_and_updates_only_its_own_items`: notes with a `you` character 王医生 (km "វ៉ាង") and an `ai` character 小美 (km ""); learned gives 王医生 km "ផ្សេង" and 小美 km "ម៉ី" and a new 刘护士 → 王医生 stays "វ៉ាង"/`you`, 小美 becomes "ម៉ី"/`ai`, 刘护士 added as `ai`; `learned` contains the job ids.
  - `test_a_removed_item_is_not_learned_again`: `apply_user_edit` with the characters list minus 小美 → gone and `"小美"` in `dismissed["characters"]`; a later `merge_learned` that offers 小美 doesn't add it; an edit that adds 小美 back clears it from `dismissed`.
  - `test_user_written_summary_and_tone_win`: summary for job `j1` by `you` survives a learned summary; tone by `you` survives; `ai` tone is replaced.
  - `test_edits_mark_changed_items_as_yours`: unchanged `ai` row stays `ai`; changed km → `you`; new row without id → `you` with an id.
  - `test_limits`: 70 learned characters → 60 kept; a 1,000-char summary stored as 400; intro over 4,000 cut.
  - `test_translation_block_is_bounded`: 80 summarised episodes, translating episode 81 → block holds exactly the 5 previous summaries ("Episode 76" … "Episode 80") and no "Episode 75".
  - `test_translation_block_names_characters_and_words`: block contains "王医生 → វ៉ាង", the speaker tag when set, and the intro; `for_translation(empty_notes(), "j", ["j"]) == ""`.
- [ ] **Step 2: Run** `pytest app/tests/test_story_notes.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** `story_notes.py`. Block wording starts `ABOUT THIS DRAMA — from the user and from episodes already checked. Keep these names, genders and forms of address.`, then labelled parts (Intro, Tone, Characters `- 王医生 → វ៉ាង (SPEAKER_03): about`, Words to keep `- 主任 → ប្រធាន — note`, Story so far `- Episode N: summary`). `for_assistant` uses every summary and the heading `STORY NOTES FOR THIS PROJECT`.
- [ ] **Step 4: Run** → PASS. Wire `with_intro` into `create_project` and add `test_a_new_project_keeps_its_intro` (POST `/api/v1/projects/` with `story_intro` → `projects.story_notes["intro"]` set) to `test_story_learning.py`; run → PASS.
- [ ] **Step 5: Commit** `feat(story): story notes — merge, your edits win, prompt blocks`.

### Task 3: Learning from finished episodes, and the story API

**Files:**
- Create: `backend/app/services/story_learning.py`, `backend/app/api/routes/story.py` (mount in `main.py` next to `projects.router`)
- Modify: `backend/app/api/routes/jobs.py` (new `PATCH /{job_id}/finished`, next to `update_job_title` L428-444), `backend/app/services/pipeline_common.py` (+ `episode_ids`), `backend/app/services/chat_agent.py` (`_episode_order` → use `pipeline_common.episode_ids`), `backend/app/schemas/schemas.py`
- Test: `backend/app/tests/test_story_learning.py`

**Interfaces:**
- Consumes: Task 2's functions; `gemini_models.generate_content(purpose, payload, *, timeout)`; `translator.GeminiError` / `translator._gemini_with_retries` for retrying 429/5xx.
- Produces:
  - `pipeline_common.episode_ids(db, project_id) -> list[str]` (job ids by `created_at`; index+1 = episode number)
  - `story_learning.learning_state(project_id) -> dict` → `{"state": "idle"|"running"|"failed", "message": str}`; `claim(project_id) -> bool`; `async learn_from_finished(project_id: str) -> None` (releases the claim in `finally`); `async _ask_gemini(payload: dict) -> dict` (patch point; parsed JSON or raises); `LEARN_BATCH = 5`
  - Schemas: `StoryText{text, by}`, `StoryCharacter{id, zh, km, about, speaker, by}`, `StoryWord{id, zh, km, note, by}`, `StoryEpisode{job_id, number, title, finished, learned, summary, summary_by}`, `StoryLearning{state, message}`, `StoryView{intro, tone, characters, words, episodes, learning, can_learn}`, `StoryCharacterIn{id: Optional, zh, km, about, speaker=""}`, `StoryWordIn{id: Optional, zh, km, note=""}`, `StoryEdit{intro?, tone?, characters?, words?, summaries?: dict[str, str]}` (field max lengths = the limits), `JobFinishedUpdate{finished: bool}`
  - Routes: `GET /api/v1/projects/{id}/story` → `StoryView`; `PATCH /api/v1/projects/{id}/story` (`StoryEdit`) → `StoryView`; `POST /api/v1/projects/{id}/story/learn` → `StoryView` (202; 400 without a Gemini key; 409 while running); `PATCH /api/v1/jobs/{job_id}/finished` → `JobResponse` — finishing sets `finished_at` (naive UTC) and, when a key is set and `claim()` succeeds, schedules `learn_from_finished`; un-finishing clears it and learns nothing.
- Learning flow: read the project's finished-but-unlearned jobs in episode order; per batch of `LEARN_BATCH`: set state `running` / message `Reading episodes 1–5…`; read lines (start-time order; `[SPEAKER_03 · Dr. Wang] 中文 → ខ្មែរ`, Chinese only when Khmer is empty; skip an episode with no lines but mark it learned); close the session; `_ask_gemini` with `systemInstruction` (learning instructions), the current notes rendered by `for_assistant` with `(you)` marks, the lines, `responseMimeType: application/json` and a `responseSchema` of `{tone, characters[{zh, km, about, speaker}], words[{zh, km, note}], summaries[{episode, summary}]}`; then open a session, `select(Project).with_for_update()`, `merge_learned` into the **current** notes, `flag_modified`, commit. Map `summaries[].episode` back to the batch's job ids; ignore numbers outside the batch. Any failure → state `failed` with a short reason; earlier batches stay.

- [ ] **Step 1: Write the failing tests** (patch `story_learning._ask_gemini`; use the `background_db` pattern from `test_speaker_pipeline.py` so the background run uses the test DB; set a key with monkeypatch):
  - `test_finishing_an_episode_learns_from_it`: project + 2 jobs with segments; PATCH finished on job 1 → `GET /story` shows the learned character, episode 1 `finished`/`learned` with its summary, `learning.state == "idle"`; the fake saw only episode 1's lines.
  - `test_first_run_reads_finished_episodes_together_in_order`: 3 finished jobs, then `POST /story/learn` → one `_ask_gemini` call listing Episode 1, 2, 3 in order.
  - `test_an_edit_made_while_learning_survives`: the fake `_ask_gemini` applies a user PATCH (via a second session) before returning → the user's change and the learned items are both there.
  - `test_a_failed_run_keeps_the_notes_and_says_why`: fake raises → `learning.state == "failed"`, message non-empty, notes unchanged, `learned` empty.
  - `test_learning_needs_a_key`: no key → `POST /story/learn` 400; PATCH finished still 200 and `can_learn` false.
  - `test_unfinishing_does_not_unlearn`: finish, learn, unfinish → `finished` false, summary kept.
  - `test_story_edit_marks_items_as_yours`: PATCH `/story` with a changed character → `by == "you"`; an intro of 4,001 chars → 422.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the service, schemas and routes per the Interfaces above. `GET /story` builds `episodes` from all jobs (number, title, `finished_at is not None`, `job_id in learned`, summary) and `can_learn = bool(settings.GEMINI_API_KEY)`.
- [ ] **Step 4: Run** the file → PASS; full backend suite → green (fix `chat_agent` imports after the `episode_ids` move).
- [ ] **Step 5: Commit** `feat(story): learn story notes from finished episodes`.

### Task 4: Notes in translation and in the Assistant

**Files:**
- Modify: `backend/app/services/translator.py` (`translate_batch_with_report` L498, `_translate_chunk` L468, `_translate_batch_with_gemini` L278, prompt L315-378)
- Modify: `backend/app/services/pipeline.py` (L203, L277), `backend/app/services/subtitle_pipeline.py` (L201)
- Modify: `backend/app/services/chat_agent.py` (`_SYSTEM_INSTRUCTION`, `_call_gemini` L265, `run_chat_turn` L317)
- Modify tests with fixed-signature fakes: `test_speaker_pipeline.py` L111/423/441/459 (accept `story=""`), `test_chat_agent.py` fakes (accept `system=None`)
- Test: `backend/app/tests/test_story_learning.py` (or a new `test_story_translation.py`)

**Interfaces:**
- Produces: `translate_batch_with_report(texts, source_lang="zh", target_lang="km", segments_context=None, story: str = "")`; `_call_gemini(contents: list[dict], system: str = _SYSTEM_INSTRUCTION) -> dict`.
- The translation prompt gets the block (when non-empty) right before `STEP 1`, plus one line in STEP 1: "If ABOUT THIS DRAMA gives a character's gender or how they address someone, use it instead of guessing." The Assistant's system text is `_SYSTEM_INSTRUCTION + "\n\n" + for_assistant(...)` when notes say anything.

- [ ] **Step 1: Write the failing tests:**
  - `test_translation_prompt_carries_the_story`: patch `translator._post_translation` to capture the payload; `translate_batch_with_report(["你好"], segments_context=[{"speaker_label": "SPEAKER_00"}], story="ABOUT THIS DRAMA — x")` → prompt text contains the block before "STEP 1"; with `story=""` it contains no "ABOUT THIS DRAMA".
  - `test_analysis_and_missing_lines_pass_the_project_story`: project with notes (a `you` character); patch `pipeline.translate_batch_with_report` capturing kwargs; run `run_translation(job_id)` → `story` contains the character; a project without notes → `story == ""`.
  - `test_assistant_system_text_has_the_notes`: fake `_call_gemini(contents, system=None)` records `system`; project with an intro → it contains the intro; without notes → equals `_SYSTEM_INSTRUCTION`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; each call site computes `story_notes.for_translation(project.story_notes, job.id, await episode_ids(db, project.id))` while it still has a session, before the Gemini call.
- [ ] **Step 4: Run** the new tests and the full suite → green.
- [ ] **Step 5: Commit** `feat(story): story notes guide translation and the Assistant`.

### Task 5: Story tab, intro box and finished episodes (frontend)

**Files:**
- Modify: `frontend/src/types/index.ts`, `frontend/src/api/client.ts`, `frontend/src/hooks/useApi.ts`
- Create: `frontend/src/components/panels/StoryPanel.tsx`
- Modify: `frontend/src/store/editorStore.ts` (`RailTab` + `'story'`), `frontend/src/components/layout/EditorRail.tsx` (`{ tab: 'story', icon: BookOpen, label: 'Story' }` before assistant), `frontend/src/pages/EditorPage.tsx` (dock case `railTab === 'story'` → `<StoryPanel projectId={projectId} jobId={jobId} />`), `frontend/src/features/projects/CreateProjectModal.tsx`
- Modify: `CLAUDE.md` (Story notes: storage, learning, ownership, where used)

**Interfaces:**
- Consumes: Task 3's routes/schemas.
- Produces: types `StoryBy`, `StoryCharacter`, `StoryWord`, `StoryEpisode`, `StoryLearning`, `StoryView`, `StoryEdit`, `Job.finished_at?: string | null`, `ProjectCreate.story_intro?: string`; client `story.get(projectId)`, `story.update(projectId, edit)`, `story.learn(projectId)`, `jobs.setFinished(jobId, finished)`; hooks `useStory(projectId)` (key `['story', projectId]`, `refetchInterval` 2000 while `learning.state === 'running'`), `useUpdateStory()` (setQueryData), `useLearnStory()`, `useSetEpisodeFinished()` (invalidates `['story', projectId]`, `['jobs', projectId]`, `['job', jobId]`).
- Panel, top to bottom: **Your intro** (optional; textarea + Save when changed), **Tone** (one line + Save), **Characters** and **Words to keep** (rows: Chinese, Khmer in brand colour, about/note muted; an `AI` tag on `ai` rows; click a row to edit inline with Save/Cancel/Delete; `+ Add`), **Story so far** (one row per learned episode, editable), **Finished episodes** (a toggle chip per episode: `1 ✓`; tooltip "Mark episode N finished"/"Not finished"; under it the learning line — shimmer `Reading episodes 1–5…` while running, amber reason + *Try again* when failed, "Add a Gemini API key to learn from episodes" + a button that opens Settings when `!can_learn`). No project → `PanelEmptyState` "No project open". New project window: optional textarea "About this drama", placeholder "The story, the main characters and how they talk. Optional — you can add it later in Story."

- [ ] **Step 1:** Types, client and hooks; `npx tsc -b` passes.
- [ ] **Step 2:** Panel, rail tab, dock case, intro box. `npx tsc -b && npm run lint && npm test && npm run build` → green.
- [ ] **Step 3: Browser check** (preview: `DATABASE_URL=` + scratch `DUBIFY_DATA_DIR`): create a project with an intro → Story shows it; upload/mock an episode with lines, mark it finished → the shimmer line shows, then the learned rows (real Gemini key in the preview, or none → the key message); edit a character → its `AI` tag goes; light theme readable.
- [ ] **Step 4: Commit** `feat(story): Story tab, intro on new projects, finished episodes` (+ CLAUDE.md).

## Phase 2 — "This line"

### Task 6: The line reference in chat (backend)

**Files:**
- Modify: `backend/app/models/models.py` (`ChatMessage.line_ref = Column(JSON, nullable=True)`), `backend/app/core/database.py` (`("chat_messages", [("line_ref", "JSON")])`)
- Create: `backend/alembic/versions/0a1b2c3d4e5f_add_line_ref_to_chat_messages.py` (down `f9a0b1c2d3e4`)
- Modify: `backend/app/schemas/schemas.py` (`ChatSendRequest.line_id: Optional[str] = None`, `ChatMessageResponse.line_ref: Optional[dict] = None`), `backend/app/api/routes/chat.py`, `backend/app/services/chat_agent.py`
- Test: `backend/app/tests/test_chat_agent.py`

**Interfaces:**
- Produces: `run_chat_turn(db, project, user_text, *, line_id: str | None = None) -> list[ChatMessage]`; `line_ref = {"segment_id", "episode": int, "time": float, "speaker": str, "source_text": str, "khmer_text": str}` stored on the user message (a snapshot); `_messages_to_contents` renders such a message as `[This line — episode 2, 00:41.2, Dr. Wang (SPEAKER_03), segment_id …. Chinese: … | Khmer now: …]\n{text}`; the system text adds: "A message that starts with [This line …] is about that segment: 'this', 'it' and 'this line' mean it; use its segment_id with propose_edit."

- [ ] **Step 1: Failing tests:** `test_a_message_about_a_line_carries_it` (send with `line_id` → the user message's `line_ref` has episode 1, the speaker's display name, the texts; the fake saw the `[This line —` prefix); `test_a_line_from_another_project_is_refused` (404); `test_a_deleted_line_is_refused` (404, message "That line no longer exists").
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (column, migration, `add_missing_columns`, schema, route passes `line_id`). **Step 4:** Run file + full suite → green.
- [ ] **Step 5: Commit** `feat(assistant): a message can be about the line being edited`.

### Task 7: "This line" in the Assistant (frontend)

**Files:**
- Modify: `frontend/src/store/editorStore.ts` (`assistantLineId: string | null` + `setAssistantLineId`, inside `DEFAULT_STATE` so an episode switch clears it), `frontend/src/components/transcript/TranscriptPanel.tsx` (textarea `onFocus` L859-861 also calls `setAssistantLineId(seg.id)`), `frontend/src/components/panels/AssistantPanel.tsx` (props `{ projectId, jobId }`), `frontend/src/pages/EditorPage.tsx` (pass `jobId`), types/client/hooks (`ChatLineRef`, `ChatMessage.line_ref?`, send takes `lineId`), `frontend/src/lib/chatMessages.ts` (`standInMessage` takes an optional `line_ref`)
- Test: `frontend/tests/chatMessages.test.ts`

**Interfaces:**
- Produces: `standInMessage(projectId, text, now, lineRef?: ChatLineRef)`; the chip reads the segment from `useSegments(jobId)`; send variables `{ projectId, text, lineId?: string, lineRef?: ChatLineRef }`.
- Chip above the chat box: `This line · 00:41 · <first 24 chars of the Chinese>` + × (clears `assistantLineId`); hidden when the line isn't in the open episode's segments. A sent message with `line_ref` shows a small `Ep 2 · 00:41` tag above its bubble. A 404 for the line → toast "That line no longer exists" and the chip clears.

- [ ] **Step 1: Failing test** `test('a message about a line keeps the line on its stand-in')` in `chatMessages.test.ts`; run → FAIL; implement; → PASS.
- [ ] **Step 2:** UI; frontend checks green.
- [ ] **Step 3: Browser check:** click into a line's text → chip appears in the Assistant; hovering other rows doesn't change it; "make this less formal" → the reply proposes an edit for that line; × removes the chip; switching episode clears it.
- [ ] **Step 4: Commit** `feat(assistant): the Assistant knows the line you're editing` (+ CLAUDE.md line).

## Phase 3 — Chat history

### Task 8: Chat threads (backend)

**Files:**
- Modify: `backend/app/models/models.py` (new `ChatThread`: `id`, `project_id` FK, `title String(120)`, `created_at`, `updated_at`; `ChatMessage.thread_id = Column(String, ForeignKey("chat_threads.id"), nullable=True)`), `backend/app/core/database.py` (`("chat_messages", [..., ("thread_id", "VARCHAR")])`)
- Create: `backend/alembic/versions/1b2c3d4e5f6a_add_thread_id_to_chat_messages.py` (down `0a1b2c3d4e5f`)
- Modify: `backend/app/services/chat_agent.py`, `backend/app/api/routes/chat.py`, `backend/app/api/routes/projects.py` (`delete_project` also deletes threads, after messages), `backend/app/schemas/schemas.py`
- Test: `backend/app/tests/test_chat_threads.py`; update `test_chat_agent.py` for the new response shape

**Interfaces:**
- Produces: `thread_title(text: str) -> str` (first line, spaces collapsed, ≤ 60 chars with "…"); `async adopt_unthreaded(db, project_id) -> None` (messages without a thread → one thread titled from the first user message, `created_at`/`updated_at` from the first/last message); `run_chat_turn(db, project, user_text, *, thread_id: str | None = None, line_id: str | None = None) -> tuple[str, list[ChatMessage]]` — a missing `thread_id` creates the thread; history is that thread's messages only (still limited by `CHAT_HISTORY_LIMIT`); every new row gets `thread_id`; the thread's `updated_at` is bumped.
- Schemas: `ChatThreadResponse{id, project_id, title, created_at, updated_at, message_count}` (`message_count` = messages with neither `function_call` nor `function_response`); `ChatSendRequest{text, thread_id?, line_id?}`; `ChatSendResponse{thread_id, messages}`; `ChatMessageResponse.thread_id`.
- Routes: `GET /chat/{project_id}/threads` (adopts first; newest `updated_at` first); `GET /chat/threads/{thread_id}/messages`; `POST /chat/{project_id}/messages` (404 for a thread of another project); `DELETE /chat/threads/{thread_id}` → 204, deletes its messages and sets `ChatProposedEdit.message_id = NULL` for them. `GET /chat/{project_id}/messages` is removed.

- [ ] **Step 1: Failing tests:** `test_the_first_message_starts_a_titled_chat`; `test_a_chat_only_remembers_its_own_messages` (two threads; the fake's `contents` in thread B hold none of A's text); `test_earlier_messages_become_the_first_chat` (rows with NULL `thread_id` → listing returns one thread with them, titled from the first user text; listing again doesn't make another); `test_deleting_a_chat_keeps_its_pending_edits`; `test_a_chat_of_another_project_is_refused`; `test_chats_are_listed_newest_first_with_counts`; `test_thread_title` (long/multi-line text).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** File + full suite → green.
- [ ] **Step 5: Commit** `feat(assistant): chat threads per project`.

### Task 9: History and New chat (frontend)

**Files:**
- Modify: types (`ChatThread`, `ChatSendResponse.thread_id`, `ChatMessage.thread_id`), client (`chat.listThreads(projectId)`, `chat.listMessages(threadId)`, `chat.deleteThread(threadId)`, `chat.sendMessage(projectId, { text, threadId, lineId })`), hooks (`useChatThreads(projectId)` key `['chat-threads', projectId]`, `useChatMessages(threadId)` key `['chat-messages', threadId]`, `useDeleteChatThread()`, `useSendChatMessage` variables gain `threadId: string | null`; the stand-in goes under `threadId ?? 'new:' + projectId`; on success the turn is written under `data.thread_id`, the store's thread for the project becomes `data.thread_id`, `['chat-threads', projectId]` is invalidated), `frontend/src/store/editorStore.ts` (`assistantThreads: Record<string, string | null>` + `setAssistantThread(projectId, threadId)`, outside `DEFAULT_STATE`), `frontend/src/lib/chatMessages.ts` (`chatDateLabel(iso: string, now: Date): string` → `Today`, `Yesterday`, else `Oct 18`), `frontend/src/components/panels/AssistantPanel.tsx`
- Test: `frontend/tests/chatMessages.test.ts`

**Interfaces:**
- The open chat for a project: the store's value; when absent, the newest thread once threads load (none → a new chat). Header buttons: History (`Clock`) and New chat (`SquarePen`, sets the project's thread to `null`). History view slides over the chat (like the voice browser over Speakers): `New chat` button; rows with title, `chatDateLabel · N messages`, the open chat marked with the brand left border, a trash icon with the two-step confirm used for episodes; deleting the open chat opens the newest remaining one.

- [ ] **Step 1: Failing test** `test('chat dates read Today, Yesterday, then the date')`; run → FAIL; implement → PASS.
- [ ] **Step 2:** UI; frontend checks green.
- [ ] **Step 3: Browser check:** an existing project's old chat shows as the first chat; New chat → empty view, first message creates a titled chat in History; switching chats shows each one's messages; delete with confirm; tab switch mid-reply keeps "Thinking…".
- [ ] **Step 4: Commit** `feat(assistant): chat history and New chat` (+ CLAUDE.md).

## Finish

- [ ] Whole-branch review by a fresh reviewer (keyboard/focus not in scope; data ownership, migrations on an old DB, background learning and prompt bounds are), fix Critical/Important with a failing test first, full suites green, then hand the result to the user with the preview.
