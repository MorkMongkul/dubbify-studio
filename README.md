# Dubify Studio

An AI-powered movie dubbing platform that translates and dubs Chinese films into Khmer (and other languages). Upload a video, get back a fully translated script with speaker-attributed segments ready for voice synthesis.

---

## What it does

Dubify Studio automates the most painful parts of movie dubbing:

1. **Uploads** a video file (`.mp4`, `.mkv`, etc.)
2. **Detects** embedded subtitle tracks automatically — uses them if found (more accurate), falls back to AI speech recognition if not
3. **Diarizes** speakers — identifies who is talking at each moment using pyannoteAI
4. **Transcribes** speech to text using Whisper via pyannoteAI combined diarize+transcribe API
5. **Translates** each line to English and Khmer using Gemini 2.5 Flash with full conversation context — natural, emotional, dubbing-quality output
6. **Synthesizes** voice using VoxCPM2 (production-grade TTS with Khmer support, self-hosted on GPU cloud)

---

## Project structure

```
Dubify_Studio/
├── backend/   ← FastAPI Python backend
└── frontend/  ← React TypeScript frontend
```

---

## Backend

### Tech stack

| Component | Technology |
|---|---|
| Framework | FastAPI (Python 3.11+) |
| Database | SQLite (dev) / PostgreSQL (production) |
| ORM | SQLAlchemy async |
| Speaker diarization | pyannoteAI cloud API |
| ASR | pyannoteAI Whisper large-v3-turbo (combined with diarization) |
| Subtitle extraction | ffmpeg |
| Translation | Google Gemini 2.5 Flash with conversation context |
| TTS | VoxCPM2 (self-hosted on Lightning AI / RunPod) |
| Audio mixing | ffmpeg |

### Pipeline

```
Video upload
    │
    ├── Embedded subtitles found? ──YES──→ Parse SRT/ASS → skip ASR
    │                                                          │
    └──────────────────────────NO──→ pyannoteAI diarize + transcribe
                                                               │
                                                     Assign speakers to lines
                                                               │
                                                     Gemini 2.5 Flash translate
                                                     (ZH → EN + ZH → KM)
                                                               │
                                                     Save segments to database
                                                               │
                                                     VoxCPM2 TTS synthesis
                                                               │
                                                     ffmpeg audio mix → dubbed video
```

### Setup

```bash
cd backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install ffmpeg (required for audio/subtitle extraction)
brew install ffmpeg        # macOS
# apt install ffmpeg       # Ubuntu

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Run server
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/docs` for the interactive Swagger UI.

### Environment variables

```bash
# App
APP_NAME="Dubbify Studio API"
DEBUG=true
SECRET_KEY=your-secret-key

# Database — leave empty for SQLite (dev), or set PostgreSQL URL for production
DATABASE_URL=

# pyannoteAI — speaker diarization + transcription (free trial at dashboard.pyannote.ai)
PYANNOTEAI_TOKEN=your_pyannoteai_token

# Google Gemini — translation (free at aistudio.google.com)
GEMINI_API_KEY=your_gemini_api_key
TRANSLATION_BACKEND=gemini

# VoxCPM2 TTS — deploy on Lightning AI or RunPod, paste URL here
VOXCPM2_API_URL=https://your-studio.lightning.ai/tts

# CORS
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
```

### API endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Service health check |
| POST | `/api/v1/projects/` | Create project |
| GET | `/api/v1/projects/` | List all projects |
| DELETE | `/api/v1/projects/{id}` | Delete project |
| POST | `/api/v1/jobs/upload/{project_id}` | Upload video (auto-detects subtitles) |
| POST | `/api/v1/jobs/upload-subtitle/{project_id}` | Upload video + separate subtitle file |
| GET | `/api/v1/jobs/{job_id}` | Poll job status and progress |
| GET | `/api/v1/jobs/{job_id}/subtitle-tracks` | List subtitle tracks in video |
| GET | `/api/v1/jobs/{job_id}/segments` | Get all transcript segments |
| PATCH | `/api/v1/segments/{id}` | Edit segment text |
| POST | `/api/v1/segments/{id}/approve` | Approve segment for TTS |
| POST | `/api/v1/jobs/{job_id}/approve-all` | Approve all segments |
| GET | `/api/v1/projects/{id}/speakers` | List detected speakers |
| PATCH | `/api/v1/speakers/{id}` | Edit speaker voice profile |
| POST | `/api/v1/tts/synthesize/job/{job_id}` | Synthesize all approved segments |
| POST | `/api/v1/tts/mix/{job_id}` | Mix TTS audio into final video |

### Run tests

```bash
pytest tests/ -v
```

### Backend folder structure

```
backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py             ← All settings from .env
│   │   └── database.py           ← SQLAlchemy async engine
│   ├── models/models.py          ← DB tables: Project, Job, Speaker, Segment
│   ├── schemas/schemas.py        ← Pydantic request/response schemas
│   ├── services/
│   │   ├── audio_extractor.py    ← ffmpeg: extract audio + subtitles
│   │   ├── subtitle_parser.py    ← SRT/ASS subtitle parser
│   │   ├── diarizer.py           ← pyannoteAI diarization + ASR
│   │   ├── translator.py         ← Gemini translation with context
│   │   ├── tts_client.py         ← VoxCPM2 HTTP client
│   │   ├── pipeline.py           ← ASR pipeline orchestrator
│   │   └── subtitle_pipeline.py  ← Subtitle pipeline orchestrator
│   └── api/routes/
│       ├── health.py
│       ├── projects.py
│       ├── jobs.py
│       ├── segments.py
│       └── tts.py
├── tests/test_api.py
├── requirements.txt
└── .env.example
```

---

## Frontend

### Tech stack

| Component | Technology |
|---|---|
| Framework | React 19 + TypeScript |
| Build tool | Vite 8 |
| Styling | Tailwind CSS v3 |
| Routing | React Router v7 |
| HTTP client | Axios |
| Data fetching | TanStack Query v5 |
| Global state | Zustand v5 |

### Setup

```bash
cd frontend

npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies all `/api` requests to the backend at `localhost:8000` automatically — run both servers simultaneously.

```bash
npm run build    # production build
npm run preview  # preview production build locally
```

### Frontend folder structure

```
frontend/
├── src/
│   ├── api/client.ts       ← Axios API client — all backend calls
│   ├── hooks/useApi.ts     ← TanStack Query hooks for every endpoint
│   ├── store/index.ts      ← Zustand global state
│   ├── types/index.ts      ← TypeScript types matching backend schemas
│   ├── components/         ← Shared UI components
│   ├── pages/              ← Page components
│   ├── lib/                ← Utility functions
│   ├── App.tsx             ← Router setup
│   └── main.tsx            ← Entry point
├── vite.config.ts          ← Proxy + path aliases
├── tailwind.config.js
└── tsconfig.json
```

### Planned pages

| Route | Page |
|---|---|
| `/projects` | Projects dashboard — list, create, delete |
| `/projects/:id` | Project detail — jobs list, upload video |
| `/projects/:id/jobs/:jobId` | Script editor — review segments, approve for TTS |

---

## Cloud GPU setup (VoxCPM2)

VoxCPM2 requires an NVIDIA GPU with 8GB+ VRAM. It cannot run on Mac M1.

**Testing:** [Lightning AI](https://lightning.ai) — free GPU credits on signup (T4/L4)

**Production:** [RunPod](https://runpod.io) or [Vast.ai](https://vast.ai) — ~$0.30–0.75/hr

### Deploy VoxCPM2 on Lightning AI

```bash
# In Lightning AI Studio terminal
pip install voxcpm soundfile fastapi uvicorn

# Start the TTS server
python voxcpm2_server.py

# Expose port 8000 via Lightning AI Port Viewer
# Paste the HTTPS URL into VOXCPM2_API_URL in backend/.env
```

---

## Supported languages

VoxCPM2 officially supports 30 languages including:

Arabic, Burmese, Chinese, Danish, Dutch, English, Finnish, French, German, Greek, Hebrew, Hindi, Indonesian, Italian, Japanese, **Khmer**, Korean, Lao, Malay, Norwegian, Polish, Portuguese, Russian, Spanish, Swahili, Swedish, Tagalog, Thai, Turkish, Vietnamese

---

## Requirements

| Requirement | Minimum |
|---|---|
| Python | 3.11+ |
| Node.js | 18+ |
| ffmpeg | Any recent version |
| RAM (Mac) | 8GB (backend + frontend only) |
| GPU (VoxCPM2) | NVIDIA 8GB+ VRAM (cloud only) |

---

## Development notes

- All heavy ML models run as cloud APIs — nothing downloads to your Mac
- The backend mocks all AI services gracefully when API keys are missing — useful for testing without credits
- SQLite is used automatically in development — no database setup needed
- Job status is polled every 2 seconds from the frontend while a job is running

---

## License

Private — all rights reserved.
