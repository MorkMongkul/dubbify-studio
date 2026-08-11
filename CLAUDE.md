# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Dubify Studio — an AI movie-dubbing platform (primarily Chinese → Khmer). Monorepo with a FastAPI backend (`backend/`), a React SPA (`frontend/`), and a Tauri desktop shell (`frontend/src-tauri/`) that runs the backend as a bundled PyInstaller sidecar.

## Commands

### Backend (run from `backend/`)

```bash
source venv/bin/activate
uvicorn app.main:app --reload          # http://localhost:8000, Swagger at /docs
pytest app/tests/ -v                   # tests live in app/tests/, NOT tests/
pytest app/tests/test_api.py -v -k test_name   # single test
alembic upgrade head                   # apply migrations
alembic revision -m "description"      # new migration (hand-written, not autogenerate)
```

- No DB setup needed for dev: empty `DATABASE_URL` → SQLite (`khmer_dubber_dev.db`, WAL mode). Production uses Postgres/Neon via `postgresql+asyncpg://`.
- `init_db()` runs `Base.metadata.create_all` at startup, so brand-new tables appear automatically in dev — but columns added to existing tables require an alembic migration. `alembic/env.py` derives its URL from `settings.effective_db_url` (converts async drivers to sync).
- Tests use in-memory SQLite with a `get_db` override; they never need API keys because every ML service has a mock fallback.

### Frontend (run from `frontend/`)

```bash
npm run dev        # http://localhost:5173 — proxies /api, /health, /uploads to 127.0.0.1:8000
npm run lint       # eslint
npm run build      # tsc -b && vite build
```

Run both servers together for dev; the Vite proxy handles all backend calls (frontend uses relative `/api/v1` baseURL).

### Desktop (Tauri)

```bash
cd backend && ./build_sidecar.sh       # PyInstaller binary → frontend/src-tauri/binaries/ (needs venv_clean with pyinstaller; bundles frontend/dist and backend/.env INTO the binary)
cd frontend && npm run tauri dev       # or: npm run tauri build
```

Rebuild the sidecar after backend changes for the packaged app to see them. Frozen-build path quirks (`sys._MEIPASS`) are handled in `app/core/config.py` and `app/main.py`.

Desktop-only browser APIs go through `src/lib/desktop.ts`, which degrades to a no-op in the browser so components can call it unconditionally (e.g. `chooseVideoSavePath` opens a native Save dialog in Tauri, returns `undefined` in dev). Adding a Tauri plugin needs three edits: `Cargo.toml`, `.plugin(...)` in `src-tauri/src/lib.rs`, **and** a permission entry in `src-tauri/capabilities/default.json` — miss the last one and the call fails at runtime only.

## Configuration

All backend settings live in `backend/app/core/config.py` (pydantic-settings, loaded from `backend/.env`). This file is the source of truth for env vars. **All AI is cloud-hosted over HTTPS (HF Spaces + Google AI Studio) — there is no local inference**; every service has a fallback chain that degrades to free/mock implementations when keys or URLs are absent:

- Diarization + ASR: MOSS HF Space (`DIARIZATION_MOSS_SPACE`, combined diarize+transcribe in one call, chunked past ~1750s) → mock diarizer.
- `TRANSLATION_BACKEND`: `"gemini"` (needs `GEMINI_API_KEY`) or `"deep"` (free Google Translate).
- TTS: `VOXCPM2_API_URL` (a Gradio app, e.g. an ephemeral Colab gradio.live URL) → public HF Space (`VOXCPM2_HF_SPACE_FALLBACK`) → Gemini TTS → mock silent audio.
- Separation: HF Space only (`SEPARATION_HF_SPACE` / `SEPARATION_HF_MODEL`) → no-op (original audio used as both stems).

This means the entire pipeline runs end-to-end on a laptop with zero keys (mock mode) — useful for testing API flow. The pyannoteAI, local Whisper, local Demucs, and Modal REST backends were removed (July 2026) — do not reintroduce `DIARIZATION_BACKEND`, `SEPARATION_BACKEND`, `PYANNOTEAI_TOKEN`, `WHISPER_API_URL`, `VOXCPM2_BACKEND`, or `VOXCPM2_API_KEY`.

## Architecture

### Pipeline (backend)

The dubbing pipeline is **two user-controlled stages plus on-demand steps**, orchestrated in `app/services/pipeline.py` and run as FastAPI background tasks (asyncio — no Celery/Redis queue despite the `REDIS_URL` setting):

1. **Stage 1** (`run_pipeline`, on video upload): ffmpeg audio extraction → vocal/BGM separation → stops at `STEMS_READY` status, waiting for the user.
2. **Stage 2** (`run_analysis_pipeline`, user clicks Analyze): diarization + transcription → translation → creates `Segment` rows → `COMPLETED`.
3. **TTS + mix** are separate endpoints (`app/api/routes/tts.py`), triggered per-segment or per-job; final mix composites TTS audio, BGM stem, and overlays via ffmpeg (`app/services/video_overlay.py`).

`app/services/subtitle_pipeline.py` is an alternative Stage 2 that uses embedded/uploaded subtitle tracks instead of diarization+ASR. Shared helpers (job status updates, speaker creation, orphaned-job recovery on startup) live in `pipeline_common.py`. `JobStatus` in `app/models/models.py` enumerates every stage.

### Data model

`Project` → `Job`s → `Segment`s + `Overlay`s; `Speaker`s belong to the project. `Voice` is a **workspace-global** voice library (not project-scoped). Voice resolution precedence for TTS: `Segment.voice_id` override → `Speaker.voice_id` → legacy `Speaker.voice_design_prompt`. Overlay positions/sizes are stored as **fractions (0–1) of video dimensions** so the live canvas preview and the full-resolution ffmpeg export map identically.

### Frontend state

Three distinct state layers — keep them separate:

- **Server state**: TanStack Query only. `src/api/client.ts` is the single axios layer (all endpoints), `src/hooks/useApi.ts` wraps each one in a query/mutation hook. Job progress is polled via `refetchInterval`.
- **UI-only editor state**: `src/store/editorStore.ts` (Zustand) — playback, timeline zoom, selection, plus `segmentPositions` for optimistic drag positions ahead of server confirmation.
- **Undo/redo**: `src/store/historyStore.ts` — because editor state is server-persisted, each history entry is a pair of async inverse **API calls**, not local snapshots. Any new editor mutation that should be undoable must push an entry via the helpers in `src/lib/historyHelpers.ts`; the `isApplying` guard prevents undo/redo from re-recording itself.

The editor IS the app (CapCut-style single window, Aug 2026): `src/pages/EditorPage.tsx` renders every route (`/`, `/projects/:projectId`, `/projects/:projectId/jobs/:jobId`; anything else redirects to `/`). A far-left icon rail (`src/components/layout/EditorRail.tsx`) drives a dynamic dock panel via `railTab` in editorStore — tabs: Projects / Sessions / Speakers / Elements / Voices / Settings, implemented in `src/components/panels/*` (OverlaysPanel serves the Elements tab). There are no standalone pages or nav sidebar anymore. The center composes `VideoPlayer` + `OverlayCanvas`, right is `TranscriptPanel`, bottom is `TimelineEditor`. Rail tabs must never navigate (an auto-navigate effect bounces `/projects/X` to its latest job); only rows inside panels navigate. `railTab` deliberately lives outside `DEFAULT_STATE` so `resetEditor()` (fired per session switch) can't wipe it; routes are keyed by pathname, so EditorPage remounts on every navigation and re-derives the tab on mount. Path alias `@/` → `src/`.

### Types must stay in sync

Backend Pydantic schemas (`app/schemas/schemas.py`) ↔ frontend types (`src/types/index.ts`) are maintained by hand. When changing an API shape, update: models.py (+ migration) → schemas.py → route → types/index.ts → client.ts → useApi.ts.

## Gotchas

- `max_speakers` (the "spk" input next to Analyze) is plumbed end-to-end but ignored — it was built for pyannoteAI, and the MOSS Space has no speaker-count parameter.
- SQLite dev DB is capped at one writer (pool_size=1, WAL); long background pipelines write status updates, so avoid adding long-lived transactions — commit (releasing the connection) before any long `await` on an outbound AI call, as the TTS paths do.
- `backend/uploads/` holds all media (per-project/job subdirectories) and is served at `/uploads`; segment/voice audio paths in the DB point into it.
