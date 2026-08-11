// src/api/client.ts
// Centralised API client — all backend calls go through here

import axios from 'axios'
import type {
  Project, ProjectCreate, ProjectUpdate,
  Job, PipelineStartResponse,
  Speaker, SpeakerUpdate, SpeakerCreate,
  Segment, SegmentUpdate, SegmentCreate,
  TTSResponse, HealthResponse,
  Voice, VoiceMode,
  Overlay, OverlayUpdate, OverlayTemplate,
} from '@/types'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
})

// Guard: never let a non-JSON body through as if it were data.
//
// In the packaged desktop app the webview briefly lives on tauri://localhost
// while the sidecar is still starting. Unknown paths there resolve to the SPA's
// index.html, so `/api/v1/projects/` answered 200 with an HTML *string* — which
// sailed through as `data` and turned `projects.map(...)` into a hard crash,
// while the UI cheerfully reported "No projects yet". Treat it as the failed
// request it actually is so React Query surfaces an error instead.
api.interceptors.response.use(
  (r) => {
    const type = String(r.headers?.['content-type'] ?? '')
    if (r.data !== undefined && r.data !== null && !type.includes('json') && !(r.data instanceof Blob)) {
      return Promise.reject(
        Object.assign(new Error('Backend not reachable yet — got a non-JSON response.'), {
          response: r,
          code: 'ERR_NOT_JSON',
        })
      )
    }
    return r
  },
  (err) => {
    // Extract the backend `detail` field (FastAPI standard error format)
    const detail =
      err?.response?.data?.detail ||
      err?.response?.data?.message ||
      err?.response?.statusText
    if (detail && typeof detail === 'string') {
      err.message = detail
    } else if (detail && typeof detail === 'object') {
      // FastAPI sometimes returns detail as an array of validation errors
      err.message = JSON.stringify(detail)
    }
    return Promise.reject(err)
  }
)

// ── Health ────────────────────────────────────────────────────
export const health = {
  check: () =>
    axios.get<HealthResponse>('/health').then((r) => r.data),
}

// ── Projects ──────────────────────────────────────────────────
export const projects = {
  list: () =>
    api.get<Project[]>('/projects/').then((r) => r.data),

  get: (id: string) =>
    api.get<Project>(`/projects/${id}`).then((r) => r.data),

  create: (data: ProjectCreate) =>
    api.post<Project>('/projects/', data).then((r) => r.data),

  update: (id: string, data: ProjectUpdate) =>
    api.patch<Project>(`/projects/${id}`, data).then((r) => r.data),

  delete: (id: string) =>
    api.delete(`/projects/${id}`),

  uploadLogo: (id: string, file: File) => {
    const form = new FormData()
    form.append('logo', file)
    return api.post<Project>(`/projects/${id}/logo`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },

  deleteLogo: (id: string) =>
    api.delete<Project>(`/projects/${id}/logo`).then((r) => r.data),
}

// ── Jobs ──────────────────────────────────────────────────────
export const jobs = {
  upload: (projectId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.post<PipelineStartResponse>(
      `/jobs/upload/${projectId}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 600_000 }
    ).then((r) => r.data)
  },

  uploadWithSubtitle: (projectId: string, video: File, subtitle: File) => {
    const form = new FormData()
    form.append('video', video)
    form.append('subtitle', subtitle)
    return api.post<PipelineStartResponse>(
      `/jobs/upload-subtitle/${projectId}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 600_000 }
    ).then((r) => r.data)
  },

  get: (jobId: string) =>
    api.get<Job>(`/jobs/${jobId}`).then((r) => r.data),

  listByProject: (projectId: string) =>
    api.get<Job[]>(`/jobs/project/${projectId}`).then((r) => r.data),

  delete: (jobId: string) =>
    api.delete(`/jobs/${jobId}`),

  getSubtitleTracks: (jobId: string) =>
    api.get(`/jobs/${jobId}/subtitle-tracks`).then((r) => r.data),

  analyze: (jobId: string, maxSpeakers?: number | null) =>
    api.post(`/jobs/${jobId}/analyze`, null, {
      params: maxSpeakers ? { max_speakers: maxSpeakers } : undefined,
    }).then((r) => r.data),
}

// ── Speakers ──────────────────────────────────────────────────
export const speakers = {
  listByProject: (projectId: string) =>
    api.get<Speaker[]>(`/projects/${projectId}/speakers`).then((r) => r.data),

  create: (projectId: string, data: SpeakerCreate) =>
    api.post<Speaker>(`/projects/${projectId}/speakers`, data).then((r) => r.data),

  update: (speakerId: string, data: SpeakerUpdate) =>
    api.patch<Speaker>(`/speakers/${speakerId}`, data).then((r) => r.data),
}

// ── Segments ──────────────────────────────────────────────────
export const segments = {
  listByJob: (jobId: string) =>
    api.get<Segment[]>(`/jobs/${jobId}/segments`).then((r) => r.data),

  create: (jobId: string, data: SegmentCreate) =>
    api.post<Segment>(`/jobs/${jobId}/segments`, data).then((r) => r.data),

  update: (segmentId: string, data: SegmentUpdate) =>
    api.patch<Segment>(`/segments/${segmentId}`, data).then((r) => r.data),

  delete: (segmentId: string) =>
    api.delete(`/segments/${segmentId}`),

  approve: (segmentId: string) =>
    api.post<Segment>(`/segments/${segmentId}/approve`).then((r) => r.data),

  approveAll: (jobId: string) =>
    api.post<{ approved: number }>(`/jobs/${jobId}/approve-all`).then((r) => r.data),
}

// ── TTS ───────────────────────────────────────────────────────
export interface BatchSynthesisStart {
  message: string
  started: number
  segment_ids: string[]
  skipped: string[]
}

/** 202 response from POST /tts/mix — `export_path` is the RESOLVED destination
 *  (server-side `~` expansion applied), or null when none was requested. */
export interface MixStart {
  success: boolean
  message: string
  job_id: string
  export_path: string | null
}

export const tts = {
  // Single-segment synthesis is synchronous on the backend — a serverless TTS
  // cold start can take minutes, so this must outlive the default 30s timeout.
  synthesizeSegment: (segmentId: string) =>
    api.post<TTSResponse>(`/tts/synthesize/segment/${segmentId}`, null, {
      timeout: 600_000,
    }).then((r) => r.data),

  // Batch + mix run as backend background tasks (202) — poll segments/job for
  // completion; the requests themselves return immediately.
  synthesizeBatch: (segmentIds: string[]) =>
    api.post<BatchSynthesisStart>('/tts/synthesize/batch', { segment_ids: segmentIds }).then((r) => r.data),

  synthesizeJob: (jobId: string) =>
    api.post(`/tts/synthesize/job/${jobId}`).then((r) => r.data),

  // exportPath: destination on the machine running the backend (the dev server
  // or the desktop sidecar — always the user's own machine). `~` is expanded
  // server-side, and a directory is accepted as well as a full file path. When
  // omitted the mix stays in uploads/ and is reached via the job's output_url.
  mixFinalAudio: (jobId: string, muteOriginal = true, exportPath?: string) =>
    api.post<MixStart>(`/tts/mix/${jobId}`, exportPath ? { export_path: exportPath } : {}, {
      params: { mute_original: muteOriginal }
    }).then((r) => r.data),
}

// ── Bulk timeline actions ───────────────────────────────────────
export const timelineActions = {
  // Speed every generated clip so its audio exactly fills its slot
  // (longer → faster, shorter → slower; server clamps to 0.5–2.0×).
  autofit: (jobId: string) =>
    api.post<{ fitted: number; skipped: number; missing: number }>(
      `/jobs/${jobId}/segments/autofit`
    ).then((r) => r.data),

  // Repack scattered clips into the minimum number of lanes.
  tidyLanes: (jobId: string) =>
    api.post<{ changed: number; lanes: number }>(
      `/jobs/${jobId}/segments/tidy-lanes`
    ).then((r) => r.data),
}

// ── Overlays (draggable/resizable canvas layers) ────────────────
export const overlays = {
  listByJob: (jobId: string) =>
    api.get<Overlay[]>(`/jobs/${jobId}/overlays`).then((r) => r.data),

  createImage: (jobId: string, file: File) => {
    const form = new FormData()
    form.append('media', file)
    return api.post<Overlay>(`/jobs/${jobId}/overlays/image`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },

  createFromProjectLogo: (jobId: string) =>
    api.post<Overlay>(`/jobs/${jobId}/overlays/from-project-logo`).then((r) => r.data),

  createSubtitle: (jobId: string) =>
    api.post<Overlay>(`/jobs/${jobId}/overlays/subtitle`, {}).then((r) => r.data),

  createShape: (jobId: string, data: { blur?: boolean } = {}) =>
    api.post<Overlay>(`/jobs/${jobId}/overlays/shape`, data).then((r) => r.data),

  update: (id: string, data: OverlayUpdate) =>
    api.patch<Overlay>(`/overlays/${id}`, data).then((r) => r.data),

  delete: (id: string) =>
    api.delete(`/overlays/${id}`),
}

// ── Overlay templates (global "brand kit", reusable across episodes) ──
export const overlayTemplates = {
  list: () =>
    api.get<OverlayTemplate[]>('/overlay-templates').then((r) => r.data),

  create: (name: string, fromJobId: string, setDefault = false) =>
    api.post<OverlayTemplate>('/overlay-templates', {
      name, from_job_id: fromJobId, set_default: setDefault,
    }).then((r) => r.data),

  update: (id: string, data: { name?: string; is_default?: boolean }) =>
    api.patch<OverlayTemplate>(`/overlay-templates/${id}`, data).then((r) => r.data),

  delete: (id: string) =>
    api.delete(`/overlay-templates/${id}`),

  applyToJob: (id: string, jobId: string) =>
    api.post<Overlay[]>(`/overlay-templates/${id}/apply/${jobId}`).then((r) => r.data),
}

// ── Voices (Voice Creator library) ────────────────────────────
export interface VoiceCreateInput {
  name: string
  mode: VoiceMode
  description?: string
  reference_transcript?: string
  cfg_value?: number
  inference_timesteps?: number
  reference_audio?: File | null
}

function voiceFormData(data: Partial<VoiceCreateInput>): FormData {
  const form = new FormData()
  if (data.name !== undefined) form.append('name', data.name)
  if (data.mode !== undefined) form.append('mode', data.mode)
  if (data.description !== undefined) form.append('description', data.description)
  if (data.reference_transcript !== undefined) form.append('reference_transcript', data.reference_transcript)
  if (data.cfg_value !== undefined) form.append('cfg_value', String(data.cfg_value))
  if (data.inference_timesteps !== undefined) form.append('inference_timesteps', String(data.inference_timesteps))
  if (data.reference_audio) form.append('reference_audio', data.reference_audio)
  return form
}

export const voices = {
  list: () =>
    api.get<Voice[]>('/voices/').then((r) => r.data),

  create: (data: VoiceCreateInput) =>
    api.post<Voice>('/voices/', voiceFormData(data), {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data),

  update: (id: string, data: Partial<Pick<Voice, 'name' | 'mode' | 'description' | 'reference_transcript' | 'cfg_value' | 'inference_timesteps' | 'seed'>>) =>
    api.patch<Voice>(`/voices/${id}`, data).then((r) => r.data),

  uploadReference: (id: string, file: File) => {
    const form = new FormData()
    form.append('reference_audio', file)
    return api.post<Voice>(`/voices/${id}/reference`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },

  delete: (id: string) =>
    api.delete(`/voices/${id}`),

  // Returns a WAV blob for the preview player
  preview: (id: string, text: string) =>
    api.post(`/voices/${id}/preview`, { text }, {
      responseType: 'blob',
      timeout: 300_000,   // cold-start tolerant
    }).then((r) => r.data as Blob),
}

export default api
