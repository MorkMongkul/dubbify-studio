# Dubify Studio Backend API

Production-grade FastAPI backend for the movie dubbing task.

## Pipeline stages

```
Video upload → Audio extraction (ffmpeg)
            → Speaker diarization (pyannote)
            → ASR transcription (Whisper large-v3)
            → Translation (NLLB-200: ZH→EN + ZH→KM)
            → TTS synthesis (VoxCPM2 on GPU cloud)
            → Audio mixing (ffmpeg) → Dubbed .mp4
```

## Quick start 

```bash
# 1. Clone and enter project
cd dubify Studio 

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — at minimum set HF_TOKEN and VOXCPM2_API_URL

# 5. Run the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

## Run tests

```bash
pytest tests/ -v
```

## API overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET    | `/health` | Service health check |
| POST   | `/api/v1/projects/` | Create project |
| GET    | `/api/v1/projects/` | List projects |
| POST   | `/api/v1/jobs/upload/{project_id}` | Upload video + start pipeline |
| GET    | `/api/v1/jobs/{job_id}` | Poll job status and progress |
| GET    | `/api/v1/jobs/{job_id}/segments` | Get transcript segments |
| PATCH  | `/api/v1/segments/{segment_id}` | Edit translation text |
| POST   | `/api/v1/segments/{segment_id}/approve` | Approve segment for TTS |
| PATCH  | `/api/v1/speakers/{speaker_id}` | Edit speaker voice profile |
| POST   | `/api/v1/tts/synthesize/segment/{id}` | Synthesize one segment |
| POST   | `/api/v1/tts/synthesize/job/{job_id}` | Synthesize all approved segments |
| POST   | `/api/v1/tts/mix/{job_id}` | Mix TTS audio into final video |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HF_TOKEN` | Yes (production) | HuggingFace token for pyannote |
| `VOXCPM2_API_URL` | Yes (production) | VoxCPM2 server URL on Lightning AI |
| `DATABASE_URL` | No | PostgreSQL URL (SQLite used if empty) |
| `WHISPER_API_URL` | No | Remote Whisper server (runs locally if empty) |
| `TRANSLATION_BACKEND` | No | `nllb` (default) or `deepl` |

## Running without GPU (mock mode)

All heavy ML services have mock fallbacks:
- No `HF_TOKEN` → mock diarizer (2 fake speakers)
- No `VOXCPM2_API_URL` → mock TTS (silent audio files)
- No Whisper installed → mock transcriber (sample Chinese text)

This lets you develop and test the full API flow on your Mac M1
without any GPU or paid API keys.

## Project structure

```
dubify Studio/
├── app/
│   ├── main.py               FastAPI app + router registration
│   ├── core/
│   │   ├── config.py         All settings from .env
│   │   └── database.py       SQLAlchemy async engine
│   ├── models/
│   │   └── models.py         DB tables: Project, Job, Speaker, Segment
│   ├── schemas/
│   │   └── schemas.py        Pydantic request/response schemas
│   ├── services/
│   │   ├── audio_extractor.py  ffmpeg wrapper
│   │   ├── diarizer.py         pyannote speaker diarization
│   │   ├── transcriber.py      Whisper ASR
│   │   ├── translator.py       NLLB-200 translation
│   │   ├── tts_client.py       VoxCPM2 HTTP client
│   │   └── pipeline.py         Master pipeline orchestrator
│   └── api/routes/
│       ├── health.py
│       ├── projects.py
│       ├── jobs.py
│       ├── segments.py
│       └── tts.py
├── tests/
│   └── test_api.py           
├── requirements.txt
└── .env.example
```
