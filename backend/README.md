# Dubify Studio — Backend API

FastAPI backend for the movie dubbing studio. All AI runs in the cloud (Hugging Face Spaces + Google AI Studio); locally it's just FastAPI, SQLite/Postgres, and ffmpeg.

## Pipeline

Two user-controlled stages plus on-demand steps, all running as in-process FastAPI background tasks (no worker queue):

```
Stage 1 (on upload):   ffmpeg audio extraction → vocal/BGM separation (HF Space)
                       → STEMS_READY (waits for the user)
Stage 2 (on Analyze):  MOSS HF Space diarize + transcribe (or embedded subtitles)
                       → Gemini translation (batched, scene context) → COMPLETED
On demand:             VoxCPM2 TTS per segment/batch/job → ffmpeg mix + overlays
                       → dubbed .mp4 (optionally copied to a user-chosen path)
```

Jobs left mid-stage by a server restart are marked failed on startup (`pipeline_common.recover_orphaned_jobs`) — background tasks can't survive a restart.

## Data model

`Project` → `Job`s (dubbing sessions) → `Segment`s + `Overlay`s. `Speaker`s are project-scoped; `Voice` is a **workspace-global** voice library. Voice resolution for TTS: segment override → speaker voice → legacy speaker prompt. Overlay positions/sizes are stored as **fractions (0–1) of video dimensions**, so the live canvas preview and the full-resolution ffmpeg export map identically.

For the **subtitle** overlay the stored box means something slightly different: its width is the text column, and its *bottom edge* (`y + height`) is the anchor line every subtitle rests on. One box serves every segment of a film, so `render_subtitle_pngs` gives each segment a PNG only as tall as its own wrapped text and bottom-anchors it — longer lines grow upward at an unchanged `font_size` rather than being squeezed. Font only shrinks as a backstop, past `_MAX_BLOCK_FRACTION` (40%) of frame height.

## Quick start

```bash
cd backend

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # all keys optional — mock mode works without any

uvicorn app.main:app --reload
```

Open **http://localhost:8000/docs** for the interactive Swagger UI (the complete, always-current API reference).

## Run tests

```bash
pytest app/tests/ -v      # in-memory SQLite; every AI service falls back to mocks
```

## API overview

Representative endpoints — see `/docs` for the full surface.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET    | `/health` | Service health check |
| POST   | `/api/v1/projects/` | Create project |
| GET    | `/api/v1/projects/` | List projects |
| PATCH  | `/api/v1/projects/{project_id}` | Rename / edit project (inline rename in the editor header) |
| POST   | `/api/v1/jobs/upload/{project_id}` | Upload video + start Stage 1 (auto-detects embedded subtitles) |
| POST   | `/api/v1/jobs/upload-subtitle/{project_id}` | Upload video + separate subtitle file |
| POST   | `/api/v1/jobs/{job_id}/analyze` | Start Stage 2 (diarize/transcribe/translate) |
| GET    | `/api/v1/jobs/{job_id}` | Poll job status and progress |
| GET    | `/api/v1/jobs/{job_id}/segments` | Get transcript segments |
| POST   | `/api/v1/jobs/{job_id}/segments` | Add a segment manually |
| PATCH  | `/api/v1/segments/{segment_id}` | Edit segment (text, timing, voice, effects; text edits clear stale TTS) |
| POST   | `/api/v1/jobs/{job_id}/approve-all` | Approve every segment for bulk synthesis |
| POST   | `/api/v1/tts/synthesize/segment/{id}` | Synthesize one segment (synchronous) |
| POST   | `/api/v1/tts/synthesize/batch` | Synthesize selected segments (background task) |
| POST   | `/api/v1/tts/mix/{job_id}` | Mix + export final video (background task; accepts `export_path`, `~` expanded) |
| GET/POST | `/api/v1/voices/…` | Workspace voice library (design / clone / ultimate) |
| GET/POST | `/api/v1/jobs/{job_id}/overlays/…` | Logo / subtitle / shape overlay layers |

## Environment variables

Source of truth: `app/core/config.py`. All optional — every service has a free/mock fallback.

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Google AI Studio — translation + Gemini TTS fallback |
| `GEMINI_MODEL` | Translation model (default `gemini-2.5-flash-lite`) |
| `VOXCPM2_API_URL` | Gradio app URL running VoxCPM; blank → public HF Space |
| `SEPARATION_HF_SPACE` / `SEPARATION_HF_MODEL` | Vocal separation Space + model |
| `DIARIZATION_MOSS_SPACE` | Combined diarization+ASR Space |
| `HF_TOKEN` | Optional — raises HF rate limits |
| `DATABASE_URL` | Blank → SQLite (dev); `postgresql+asyncpg://…` for Neon |
| `UPLOAD_DIR` | Media root, served at `/uploads`. **Set an absolute path** — a relative one only resolves when the process runs from `backend/`, which the desktop sidecar does not |

Media paths recorded before `UPLOAD_DIR` was absolute are stored relative (`uploads/…`). Anything reading a stored path off the filesystem goes through `core/paths.resolve_media_path`, which resolves those against the upload root regardless of the working directory — so old rows keep working without a data migration.

## Running without any keys (mock mode)

- MOSS Space unreachable → mock diarizer (2 fake speakers, sample Chinese text)
- No VoxCPM backend + no `GEMINI_API_KEY` → mock TTS (silent audio files)
- No `GEMINI_API_KEY` → free Google Translate via deep-translator

This lets you develop and test the full API flow on a laptop with zero setup.

## Migrations

```bash
alembic upgrade head                   # apply
alembic revision -m "description"      # new migration (hand-written, not autogenerate)
```

Dev SQLite needs no setup — `init_db()` creates brand-new tables at startup; only columns added to *existing* tables need a migration.

## Project structure

```
backend/
├── app/
│   ├── main.py                 FastAPI app + routers + SPA serving (packaged builds)
│   ├── core/
│   │   ├── config.py           All settings from .env (source of truth)
│   │   ├── paths.py            Resolves stored media paths (cwd-independent)
│   │   └── database.py         Async SQLAlchemy engine (SQLite WAL / Postgres)
│   ├── models/models.py        Project, Job, Speaker, Segment, Voice, Overlay
│   ├── schemas/schemas.py      Pydantic request/response schemas
│   ├── services/
│   │   ├── audio_extractor.py  ffmpeg: extract, mix, effects, subtitle probing
│   │   ├── source_separator.py Vocal/BGM split via HF Space (+ preview/m4a copies)
│   │   ├── diarizer.py         MOSS HF Space diarize+transcribe (+ chunking >29 min)
│   │   ├── subtitle_parser.py  SRT/ASS parsing + speaker matching
│   │   ├── translator.py       Gemini batch translation (deep-translator fallback)
│   │   ├── tts_client.py       VoxCPM2 Gradio/HF Space → Gemini TTS → mock
│   │   ├── video_overlay.py    Khmer subtitle PNGs (Pillow+raqm, wrap + auto-height)
│   │   ├── pipeline.py         Stage 1 + Stage 2 orchestrators (ASR path)
│   │   ├── subtitle_pipeline.py  Stage 1 + 2 for subtitle-based jobs
│   │   └── pipeline_common.py  Shared job/speaker helpers, orphan recovery
│   └── api/routes/             health, projects, jobs, segments, tts, voices, overlays
├── app/tests/test_api.py       API integration tests (in-memory SQLite, mocked AI)
├── alembic/                    Hand-written migrations
├── sidecar_entry.py            PyInstaller entrypoint for the Tauri desktop app
├── build_sidecar.sh            Builds the desktop sidecar binary
└── requirements.txt
```
