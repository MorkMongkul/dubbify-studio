---
description: Full-stack performance audit of Movelo Studio (UI, Tauri, FastAPI, network, AI workflow, FFmpeg, memory, build)
argument-hint: [optional focus area, e.g. "timeline", "network", "ffmpeg"]
---

Act as a senior software performance engineer specializing in AI-powered desktop multimedia applications.

Perform a performance audit of this repository. If a focus area is given, go deep on it; otherwise cover every section below: $ARGUMENTS

## Application context

This application is built with:

- Tauri 2 (`frontend/src-tauri/`) — desktop shell running the backend as a PyInstaller sidecar binary
- Rust (Tauri shell only)
- React 19 + TypeScript + Vite (`frontend/src/`)
- Tailwind CSS
- FastAPI, async SQLAlchemy (`backend/app/`)
- FFmpeg (audio extraction, mixing, overlay export)

**IMPORTANT — no local AI inference.** All AI services are accessed over HTTPS: Google AI Studio (Gemini translation/TTS), Hugging Face Spaces (diarization, source separation, VoxCPM fallback), Modal (VoxCPM2 TTS), and other cloud APIs. Do NOT recommend GPU optimization, CUDA, local model loading, tokenizer reuse, VRAM optimization, or local inference improvements unless you actually detect local inference in the code. The one partial exception: `SEPARATION_BACKEND="local"` runs Demucs locally — treat that as a CPU/memory concern, not a GPU tuning target.

Optimize the complete end-to-end workflow: upload → extract → separate → diarize/transcribe → translate → TTS → mix/export.

## Ground rules

- **Read the actual code before claiming an issue exists.** Every finding must cite `file:line`. No generic advice.
- Prioritize measurable improvements over theoretical optimizations.
- Distinguish what you verified from what you suspect; label suspicions as such.
- Key files: `frontend/src/pages/EditorPage.tsx`, `frontend/src/components/timeline/TimelineEditor.tsx`, `frontend/src/components/video/` (VideoPlayer, OverlayCanvas), `frontend/src/hooks/useApi.ts`, `frontend/src/api/client.ts`, `frontend/src/store/editorStore.ts`, `backend/app/services/` (pipeline, tts_client, translator, diarizer, source_separator, audio_extractor, video_overlay), `backend/app/api/routes/`, `frontend/src-tauri/src/`, `frontend/vite.config.ts`.

## Audit areas

### 1. UI Performance

- Unnecessary re-renders (Zustand selector granularity, `subscribeWithSelector` usage, context churn, props identity)
- Expensive components (TimelineEditor, OverlayCanvas, TranscriptPanel with many segments)
- Timeline performance during drag/zoom/scroll; playback smoothness (currentTime update frequency vs. render cost)
- Lazy loading of routes/pages; virtualization of long segment lists
- Bundle size (framer-motion, wavesurfer.js, Radix — check what's actually tree-shaken)
- Memory leaks (listeners, intervals, wavesurfer instances, RAF loops not cancelled on unmount)
- Animation performance (framer-motion on layout-critical paths, non-composited CSS properties)

### 2. Tauri

- IPC overhead between webview and Rust; command batching opportunities
- Filesystem performance (media served over HTTP from sidecar vs. asset protocol)
- Sidecar startup time and its effect on app launch; blocking Rust operations on the main thread
- Background task handling

### 3. FastAPI

- Async correctness: blocking calls (subprocess, requests, heavy CPU, gradio_client) inside `async def` without a thread pool
- HTTP connection reuse (per-call clients vs. shared `httpx.AsyncClient`/session for Gemini, Modal, HF)
- Request batching; response compression (gzip middleware absent?); caching headers on `/uploads` static media
- Streaming responses for large media; timeout handling and retry strategy on outbound AI calls
- Background task model (asyncio tasks vs. queue) — starvation, SQLite single-writer contention during long pipelines

### 4. Network Performance

Inspect every API call in `frontend/src/api/client.ts` and every outbound call in `backend/app/services/`. Identify:

- Sequential requests that could run in parallel; duplicate calls (same data fetched by multiple hooks/components)
- Unnecessary uploads (re-uploading reference audio per TTS call) and repeated downloads (media re-fetched instead of cached)
- Missing request caching; large payloads; missing compression; excessive JSON serialization
- Poor timeout values (client.ts 30s default vs. long-running endpoints); missing exponential backoff
- Unnecessary polling (React Query `refetchInterval` running when no job is active, polling frequency vs. job duration)

Recommend concrete reductions in total network round trips.

### 5. AI Workflow (latency + cost)

- Repeated prompts / duplicate translation requests (re-translating unchanged segments)
- Duplicate TTS requests (re-synthesizing segments whose text/voice/params didn't change)
- Repeated subtitle generation; repeated uploads of the same reference audio to TTS backends
- Recommend: response caching, translation caching keyed on (text, target lang), generated-voice caching keyed on (text, voice_id, params, seed), request deduplication, batching (Gemini batch translation size, TTS request grouping)
- Focus on minimizing API usage, latency, and per-job cost. Account for Modal cold starts and HF Space queue times where relevant.

### 6. Video Processing (FFmpeg)

- FFmpeg efficiency: duplicate transcoding, unnecessary encode/decode cycles, `-c copy` opportunities in mixing/export
- Preview and thumbnail generation strategy; waveform data generation
- Temporary file lifecycle; streaming processing instead of full-file passes
- Hardware-accelerated encode/decode (VideoToolbox on macOS) for export — this is fair game; it is codec acceleration, not AI inference

### 7. File Management

- Unnecessary copies of media between pipeline stages; large temporary files (stems, per-segment wavs)
- Cache/upload cleanup on job or project deletion — does `uploads/` grow unbounded?
- Asset organization and storage growth over time

### 8. Memory

- Blob URL / object URL creation without `revokeObjectURL`
- ArrayBuffer and audio/video buffer lifetime (wavesurfer decode buffers, video element buffers)
- Worker lifecycle; event listeners added without cleanup in effects
- Backend memory: Demucs local separation on 8GB machines, large files read fully into memory instead of streamed

### 9. Build

- Vite optimization: code splitting per route, manual chunks, dynamic imports for heavy deps
- Dependency size audit; tree shaking effectiveness; anything bundled but unused

## Output

Produce a report with exactly these sections:

1. **Performance Score** — 0–100 with one-paragraph justification
2. **Critical Issues** — actively degrading UX or burning money today; each with `file:line`, evidence, and fix
3. **High Priority Improvements** — significant wins requiring moderate effort
4. **Quick Wins** — < 1 hour each, ordered by impact-to-effort ratio
5. **Estimated Performance Gain** — per fix where measurable (e.g. "timeline drag: ~N fewer renders/sec"), honest ranges, not fabricated precision
6. **Estimated Memory Reduction**
7. **Estimated API Cost Reduction** — fewer AI round trips, cache hit rates, batch savings
8. **Estimated Startup Improvement** — Tauri shell + sidecar + first render

Rank findings by measured or strongly evidenced impact. If a commonly recommended optimization does not apply to this codebase, omit it rather than padding the report. This is an analysis command: report findings and wait — do not start applying fixes unless the user asks afterward.
