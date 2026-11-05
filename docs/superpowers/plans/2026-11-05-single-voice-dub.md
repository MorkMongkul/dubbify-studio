# Single-voice dub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "single-voice dub" project type whose own pipeline transcribes on the Mac, translates with a free natural
Khmer adaptation, voices every line with one Edge TTS voice and exports — with no speaker work.

**Architecture:** New backend package `app/services/single_voice/` (Edge voice client, translation, pipeline) and route
module `app/api/routes/single_voice.py`; existing code gets only routing hooks (project fields, end of Stage 1, the
Analyze route, the per-line voice route). Frontend: a project-kind choice in the create modal and a "Generate voices &
export" button in the transcript panel for these projects.

**Tech Stack:** FastAPI, SQLAlchemy async, `edge-tts` 7.3.1 (LGPL-3.0), sherpa-onnx (via `local_asr`), Gemini (via
`gemini_models`), React + TanStack Query.

**Spec:** `docs/superpowers/specs/2026-11-05-single-voice-dub-design.md`

## Global Constraints

- Normal projects behave exactly as in 0.5.6: every existing test passes unchanged; existing functions keep their
  behaviour (a new optional parameter must default to today's behaviour).
- Project fields: `dub_mode` "characters" | "single_voice" (NULL = characters), `voice_name`
  "km-KH-PisethNeural" | "km-KH-SreymomNeural", `voice_rate` percent −50…+50 (default 0), `auto_export` bool (default
  False). Columns need an alembic migration AND `add_missing_columns` entries.
- Voices are kept as generated: no automatic fitting or speed-up; only the project's `voice_rate` is passed to Edge.
- No video sound goes to Gemini without a click: on a single-voice project, Gemini transcribes only from the Analyze
  button.
- Single-voice translation memory counts a line only when edited (`khmer_text != khmer_machine`), approved, or its
  episode is finished.
- A failed Edge line never exports a video with a hole: the job ends COMPLETED with "N lines have no voice".
- Over 40 minutes (`MAX_SECS` 2400): a clear warning on the job, nothing else changes.
- Release notes credit edge-tts (LGPL-3.0); requirements pin `edge-tts==7.3.1`.

## Review Focus

- A line whose Khmer is empty (translation failed): skipped by voicing, counted in the message, never exported silent
  without a warning — test in Task 4.
- Edge throttling (several 429/connection errors in a row): retried with backoff, then that line fails, the rest go on
  — test in Task 2.
- A single-voice episode re-analysed after edits: segments swapped atomically like Stage 2 (a failure keeps the old
  lines) — test in Task 3.
- The per-line "Generate Voice" in the editor on a single-voice project uses Edge, never VoxCPM/casting — test in
  Task 4.
- A normal project after all hooks: the Stage 1 end, Analyze and per-line voice behave as before — covered by the
  unchanged existing suite (run in every task).

---

### Task 1: Project kind and its settings

**Files:**
- Modify: `backend/app/models/models.py` (Project), `backend/app/core/database.py` (add_missing_columns),
  `backend/app/schemas/schemas.py` (ProjectCreate/Update/Response), `backend/app/api/routes/projects.py`
- Create: `backend/alembic/versions/<rev>_single_voice_projects.py`, `backend/app/tests/test_single_voice_project.py`

**Interfaces:**
- Produces: `Project.dub_mode`, `.voice_name`, `.voice_rate`, `.auto_export`; `single_voice.is_single_voice(project)
  -> bool` in `backend/app/services/single_voice/__init__.py`; `VOICES = {"km-KH-PisethNeural": "Piseth (man)",
  "km-KH-SreymomNeural": "Sreymom (woman)"}`.

- [ ] Tests: creating a project with `dub_mode="single_voice"` returns those fields (voice defaults to Piseth, rate 0,
  auto_export false); an unknown voice or rate 80 is 422; PATCH changes voice/rate/auto_export but not dub_mode; a
  project created without dub_mode is "characters".
- [ ] Run, see them fail; implement; run the file and the whole suite; commit.

### Task 2: Edge voice client

**Files:** Create `backend/app/services/single_voice/edge_voice.py`, test `backend/app/tests/test_edge_voice.py`;
modify `backend/requirements.txt`.

**Interfaces:**
- Produces: `async def synthesize(text: str, output_path: str, voice: str, rate: int) -> dict` returning the same dict
  shape as `tts_client.synthesize` (`success`, `audio_path`, `duration_secs`, `error`, `backend="edge"`); writes a
  real WAV (ffmpeg from Edge's MP3). Retries `ATTEMPTS` 4 with backoff on network/HTTP errors; never raises.
- [ ] Tests (Edge's `Communicate` replaced): rate 10 is sent as "+10%", −20 as "-20%"; success writes a readable WAV;
  three failures then success → success; four failures → `success False` with an error naming Edge ("Edge's voice
  service didn't answer"); empty text → failure without a request.
- [ ] Run, fail, implement, pass, suite, commit.

### Task 3: Translation and the analysis pipeline

**Files:** Create `backend/app/services/single_voice/translate.py`, `backend/app/services/single_voice/pipeline.py`,
tests `backend/app/tests/test_single_voice_pipeline.py`; modify `backend/app/services/pipeline.py` (end of
`run_pipeline`), `backend/app/api/routes/jobs.py` (`analyze_job`).

**Interfaces:**
- Produces: `async def memory(db, project_id, job_id) -> list[tuple[str, str]]` (own checked rule);
  `async def translate(texts, durations, story, memory, voice) -> tuple[list[str], TranslationReport]` — Gemini
  ("translation" purpose) per `CHUNK` 60 lines with the adaptation prompt (each line's seconds, free natural spoken
  Khmer, casual/funny where it fits, avoid gendered words or use the voice's, Story notes and examples), missing
  lines asked again once, then `translator._deep_translate_many`; `async def run_analysis(job_id, allow_gemini=False)`
  (status DIARIZING → line timing → TRANSLATING → segments swapped with `replace_job_segments`, no speaker,
  `lay_out_new` → COMPLETED; Mac transcription via `local_asr.use_mac`/`transcribe`, else Gemini only when
  `allow_gemini`, else back to STEMS_READY with "Transcription on this Mac isn't ready yet (…). Press Analyze to
  transcribe with Gemini."); then, when `project.auto_export`, `voices.voice_and_export(job_id, None)`.
- Hooks: `run_pipeline` end — a single-voice project continues (claimed like AFTER_UPLOAD) into `run_analysis`
  whatever AFTER_UPLOAD says, never on degraded stems; `analyze_job` — a single-voice project schedules
  `run_analysis(job_id, allow_gemini=True)`.
- [ ] Tests: the prompt holds each line's seconds, the Story block and examples and no WHO IS SPEAKING; memory skips a
  voiced-but-unedited line and keeps edited/approved/finished ones; run_analysis with the Mac path stubbed creates
  lines without speakers and COMPLETED; with the Mac not ready and allow_gemini False → STEMS_READY + message and
  Gemini never called; with allow_gemini True → Gemini transcription used; a failing translation keeps the old lines;
  Stage 1 of a single-voice project runs run_analysis even with AFTER_UPLOAD "wait"; a normal project unchanged.
- [ ] Run, fail, implement, pass, suite, commit.

### Task 4: Voices and export

**Files:** Create `backend/app/services/single_voice/voices.py`, `backend/app/api/routes/single_voice.py`, tests
`backend/app/tests/test_single_voice_voices.py`; modify `backend/app/api/routes/tts.py` (`_render_segment_audio`
gets `synthesize=None`; `_synthesize_segment_db` uses Edge for a single-voice project), `backend/app/main.py`
(router).

**Interfaces:**
- Produces: `async def voice_and_export(job_id: str, export_path: str | None) -> None` — status SYNTHESIZING, every line
  with Khmer and no current voice (no file, or `voice_text != khmer_text`) voiced through `_synthesize_segment_db`
  (`CONCURRENCY` 4), then, if none failed, MIXING via `_mix_job_task(job_id, True, export_path)`; otherwise COMPLETED
  with "N lines have no voice — Generate voices & export again". Route `POST /api/v1/single-voice/jobs/{job_id}/export`
  body `{export_path?: str}` → 202 (409 when busy, 400 for a normal project).
- [ ] Tests: voices only missing/outdated lines; an Edge failure on one line → no export, message with the count; all
  good → `_mix_job_task` called with the path; per-line `/tts/synthesize/segment` on a single-voice project calls Edge
  with the project's voice and rate (not `tts_client`); a normal project's per-line voice still calls `tts_client`.
- [ ] Run, fail, implement, pass, suite, commit.

### Task 5: Frontend

**Files:** Modify `frontend/src/types/index.ts`, `src/api/client.ts`, `src/hooks/useApi.ts`,
`src/features/projects/CreateProjectModal.tsx`, `src/components/transcript/TranscriptPanel.tsx`,
`src/lib/jobProgress.ts` (+ its test).
- Create modal: "What kind of project?" — *Dub each character* (today) / *One voice for everyone*; for the latter a
  voice select (Piseth (man) / Sreymom (woman)), speed (−20…+30% in steps of 10) and "Fully automatic (export without
  stopping to check)"; a one-line note that Edge's voices are Microsoft's free online service and may change.
- Transcript panel, single-voice project with lines: header button **Generate voices & export** (asks where to save,
  like Export, then calls the new route); hidden for normal projects.
- jobProgress: `synthesizing` wording "Making the voices…".
- [ ] `npm test`, `npm run lint`, `tsc -b` pass; commit.

### Task 6: Real check, docs

- [ ] On a scratch copy: a ~20-minute video (10 episode copies joined) through the real pipeline on the Mac with real
  Edge voices and translation replaced by the team's own Khmer (no Gemini cost): time, peak memory, export plays.
- [ ] CLAUDE.md: a "Single-voice dub" paragraph; commit.
