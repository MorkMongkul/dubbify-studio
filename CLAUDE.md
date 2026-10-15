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
- Tests use in-memory SQLite with a `get_db` override. `app/tests/conftest.py` blanks the API keys and points `DATABASE_URL`/`UPLOAD_DIR` at a temp dir BEFORE importing the app (env vars override `backend/.env`), so no test can reach real services or the developer's database — tests that need a "configured" key set it with `monkeypatch`. Patch AI calls at the service boundary (`pipeline.transcribe_audio`, `pipeline.translate_batch_with_report`, `asr._call_window`, `tts_client.synthesize`).
- `test_tts_concurrency.py::test_batch_segments_synthesize_concurrently` is flaky (~1 in 5–20 runs, also on older code): every session shares the one `StaticPool` connection, so concurrent workers can roll back each other's pending writes. Fix by moving tests to a file-backed SQLite with a normal pool.

### Frontend (run from `frontend/`)

```bash
npm run dev        # http://localhost:5173 — proxies /api, /health, /uploads to 127.0.0.1:8000
npm run lint       # eslint
npm run build      # tsc -b && vite build
```

Run both servers together for dev; the Vite proxy handles all backend calls (frontend uses relative `/api/v1` baseURL).

### Desktop (Tauri)

```bash
scripts/build-ffmpeg.sh aarch64-apple-darwin    # once per arch: LGPL-only ffmpeg/ffprobe → vendor/ffmpeg/<target>/ (bundled by the release script)
scripts/release-macos.sh aarch64-apple-darwin   # frontend → backend sidecar (embeds frontend/dist) → .app/.dmg; Intel: VENV=<x86_64 venv> … x86_64-apple-darwin
# CI: pushing a v* tag runs .github/workflows/desktop-release.yml (macos-15 + macos-15-intel) → draft GitHub Release
cd frontend && npm run tauri dev                # dev shell: uses Vite + a backend you run yourself (no sidecar)
```

How the packaged app runs (`src-tauri/src/lib.rs` ↔ `backend/sidecar_entry.py`): Rust picks a free port and a per-launch instance id, then starts the PyInstaller sidecar with `PORT`, `DUBIFY_DATA_DIR` (app-data folder), `DUBIFY_LOG_DIR`, `DUBIFY_INSTANCE_ID`, `DUBIFY_APP_VERSION`, `FFMPEG_PATH`/`FFPROBE_PATH` (when ffmpeg is bundled next to the executable) and a PATH that includes Homebrew. It navigates the window to the backend only once `/health/ready` echoes that instance id, and emits `backend-failed` / `backend-stopped` events that `BackendGate` shows. With `DUBIFY_DATA_DIR` set the backend keeps SQLite, uploads and the user's `settings.env` (written by `PUT /api/v1/settings`; secrets are write-only) in that folder — nothing is baked into the binary. The sidecar exits when its stdin closes (the app quit or crashed), because killing the PyInstaller one-file launcher alone leaves its Python child running. Every ffmpeg/ffprobe call goes through `app/core/binaries.py`.

The public landing page lives in `website/` (static HTML/CSS/JS, deployed by `.github/workflows/website.yml` to GitHub Pages). Its download buttons link to `releases/latest/download/Dubify-Studio-Apple-Silicon.dmg` / `Dubify-Studio-Intel.dmg`, names that the release job in `desktop-release.yml` gives the DMGs — rename one side and the downloads 404. `website/site.js` also reads the latest release from the GitHub API (version, sizes, "no release yet" state). The app icon set in `src-tauri/icons/` is generated from `src-tauri/icons/app-icon.svg` with `npx tauri icon src-tauri/icons/app-icon.svg` (run in `frontend/`; delete the `android/` and `ios/` folders it also writes). Keep it in step with `website/assets/mark.svg`.

Desktop-only browser APIs go through `src/lib/desktop.ts`, which degrades to a no-op in the browser so components can call it unconditionally (e.g. `chooseVideoSavePath` opens a native Save dialog in Tauri, returns `undefined` in dev). Adding a Tauri plugin needs three edits: `Cargo.toml`, `.plugin(...)` in `src-tauri/src/lib.rs`, **and** a permission for the page that calls it — the editor (served by the backend at `http://127.0.0.1:<port>`) gets its permissions from the runtime capability added in `start_backend` (`CapabilityBuilder`, pinned to that launch's exact origin), the bundled start-up page from `capabilities/default.json`. Miss it and the call fails at runtime only. Web content gets no shell permission (the backend is started from Rust), and a navigation guard keeps the window on those two origins.

## Configuration

All backend settings live in `backend/app/core/config.py` (pydantic-settings, loaded from `backend/.env`). This file is the source of truth for env vars. **All AI is cloud-hosted over HTTPS (HF Spaces + Google AI Studio) — there is no local inference**; every service has a fallback chain that degrades to free/mock implementations when keys or URLs are absent:

- ASR + speaker labels: `app/services/asr.py` (module docstring has the measured reasons). Timing comes from ffmpeg `silencedetect` — never from Gemini's own timestamps, which drift. Clips go to Gemini (`GEMINI_ASR_MODEL`) a whole scene per request (≤40 clips / 300 s), each preceded by a `Clip k (d s):` marker, as FLAC, with structured JSON output; one request both transcribes and labels speakers, and a clip may return several turns (split by character share). Known voices are passed as reference clips (`R1…`) — earlier windows' voices plus the project's named/voiced speakers (`pipeline_common.build_speaker_references`) — and new voices get `SPEAKER_XX` labels not yet taken in the project. Chinese output is normalised to Simplified with OpenCC (Gemini drifts into traditional). Requests have wall-clock timeouts, retry/backoff on 429/5xx, split-in-half retry, and a circuit breaker. `app/services/diarizer.py` (MOSS) is unused except for the shared `DiarizedSegment` type.
- `TRANSLATION_BACKEND`: `"gemini"` (needs `GEMINI_API_KEY`) or `"deep"` (free Google Translate).
- TTS: `VOXCPM2_API_URL` (a Gradio app, e.g. an ephemeral Colab gradio.live URL) → public HF Space (`VOXCPM2_HF_SPACE_FALLBACK`) → Gemini TTS → mock silent audio. Silent mock audio is only accepted in pure mock mode (no engine configured at all — the HF Space counts); otherwise it is a failure and nothing is saved. Both synthesis paths go through `tts._render_segment_audio`, which writes to a temp file and only replaces `seg_<id>_raw.wav` (what effect changes re-render from) on real success. Design voices are baked only from VoxCPM output.
- Separation: HF Space only (`SEPARATION_HF_SPACE`) → no-op (original audio used as both stems).

This means the entire pipeline runs end-to-end on a laptop with zero keys (mock mode) — useful for testing API flow. Translation never stores the source text as a "translation": `translator.translate_batch_with_report` leaves failed lines empty and counts fallback/failed lines, which the pipelines put in the job's warning. The pyannoteAI, local Whisper, local Demucs, and Modal REST backends were removed (July 2026) — do not reintroduce `DIARIZATION_BACKEND`, `SEPARATION_BACKEND`, `PYANNOTEAI_TOKEN`, `WHISPER_API_URL`, `VOXCPM2_BACKEND`, or `VOXCPM2_API_KEY`.

## Architecture

### Pipeline (backend)

The dubbing pipeline is **two user-controlled stages plus on-demand steps**, orchestrated in `app/services/pipeline.py` and run as FastAPI background tasks (asyncio, in-process — no job queue):

1. **Stage 1** (`run_pipeline`, on video upload): ffmpeg audio extraction → vocal/BGM separation → stops at `STEMS_READY` status, waiting for the user.
2. **Stage 2** (`run_analysis_pipeline`, user clicks Analyze): transcription (no diarization, see `app/services/asr.py`) → translation → creates `Segment` rows → `COMPLETED`.
3. **TTS + mix** are separate endpoints (`app/api/routes/tts.py`), triggered per-segment or per-job; final mix composites TTS audio, BGM stem, and overlays via ffmpeg (`app/services/video_overlay.py`).

`app/services/subtitle_pipeline.py` is an alternative Stage 2 that uses embedded/uploaded subtitle tracks instead of ASR (also skips diarization, same as the main ASR pipeline). Shared helpers (job status updates, speaker creation, orphaned-job recovery on startup) live in `pipeline_common.py`. `JobStatus` in `app/models/models.py` enumerates every stage.

### Data model

`Project` → `Job`s → `Segment`s + `Overlay`s; `Speaker`s belong to the project. `Voice` is a **workspace-global** voice library (not project-scoped). Voice resolution precedence for TTS: `Segment.voice_id` override → `Speaker.voice_id` → legacy `Speaker.voice_design_prompt`. Overlay positions/sizes are stored as **fractions (0–1) of video dimensions** so the live canvas preview and the full-resolution ffmpeg export map identically.

### Frontend state

Three distinct state layers — keep them separate:

- **Server state**: TanStack Query only. `src/api/client.ts` is the single axios layer (all endpoints), `src/hooks/useApi.ts` wraps each one in a query/mutation hook. Job progress is polled via `refetchInterval`.
- **UI-only editor state**: `src/store/editorStore.ts` (Zustand) — playback, timeline zoom, selection, plus `segmentPositions` for optimistic drag positions ahead of server confirmation.
- **Undo/redo**: `src/store/historyStore.ts` — because editor state is server-persisted, each history entry is a pair of async inverse **API calls**, not local snapshots. Any new editor mutation that should be undoable must push an entry via the helpers in `src/lib/historyHelpers.ts`; the `isApplying` guard prevents undo/redo from re-recording itself.

The editor IS the app (CapCut-style single window, Aug 2026): `src/pages/EditorPage.tsx` renders every route (`/`, `/projects/:projectId`, `/projects/:projectId/jobs/:jobId`; anything else redirects to `/`). A far-left icon rail (`src/components/layout/EditorRail.tsx`) drives a dynamic dock panel via `railTab` in editorStore — tabs: Projects (episodes are listed inline under each project) / Speakers / Elements / Voices / Assistant / Settings, implemented in `src/components/panels/*` (OverlaysPanel serves the Elements tab). There are no standalone pages or nav sidebar anymore. The center composes `VideoPlayer` + `OverlayCanvas`, right is `TranscriptPanel`, bottom is `TimelineEditor`. Rail tabs must never navigate (an auto-navigate effect bounces `/projects/X` to its latest job); only rows inside panels navigate. `railTab` deliberately lives outside `DEFAULT_STATE` so `resetEditor()` (fired per session switch) can't wipe it; routes are keyed by pathname, so EditorPage remounts on every navigation and re-derives the tab on mount. Path alias `@/` → `src/`.

### Types must stay in sync

Backend Pydantic schemas (`app/schemas/schemas.py`) ↔ frontend types (`src/types/index.ts`) are maintained by hand. When changing an API shape, update: models.py (+ migration) → schemas.py → route → types/index.ts → client.ts → useApi.ts.

## Gotchas

- `max_speakers` (the "spk" input next to Analyze) is plumbed end-to-end but ignored — Gemini decides how many voices it hears.
- Stage 2 can re-run (`POST /jobs/{id}/analyze` from `stems_ready`, `completed` or `failed`): the route claims the job under a row lock (409 when busy), and the pipeline swaps segments atomically (`pipeline_common.replace_job_segments`) — a failure keeps the old segments and leaves the job COMPLETED with a message (`restore_or_fail_job`). `POST /jobs/{id}/detect-speakers` relabels existing segments only (fresh transcription mapped by time overlap, `asr.labels_for_spans`); text, edits and audio are untouched. Restart recovery returns jobs that have segments to COMPLETED instead of FAILED.
- Routes take the DB session as `Depends(get_db, scope="function")`: with the default request scope the session (and SQLite's only connection) stays open until the route's BackgroundTasks finish, starving them. Keep that scope on new routes.
- The API has no login and listens on 127.0.0.1: `TrustedHostMiddleware` (`ALLOWED_HOSTS`) blocks DNS rebinding, the SPA catch-all in `main.py` only serves files inside the build, a client-supplied `tts_audio_path` must be inside the segment's own `tts/` folder, `export_path` must be an `.mp4` under the home folder or an external drive, and overlay colours are validated (they go into ffmpeg filter graphs). Never accept other server file paths from the client.
- SQLite dev DB is capped at one writer (pool_size=1, WAL); long background pipelines write status updates, so avoid adding long-lived transactions — commit (releasing the connection) before any long `await` on an outbound AI call, as the TTS paths do.
- The desktop app bundles an **LGPL-only ffmpeg** (`scripts/build-ffmpeg.sh`): no GPL filters (e.g. `boxblur`, `eq`, `hqdn3d`, `smartblur`) and no x264 — H.264 goes through `h264_videotoolbox`. Any new filter or encoder must exist in that build, or exports fail in the desktop app only (`test_export_filters.py` guards the blur box; check with `vendor/ffmpeg/<target>/ffmpeg -filters`).
- `backend/uploads/` holds all media (per-project/job subdirectories) and is served at `/uploads`; segment/voice audio paths in the DB point into it.
