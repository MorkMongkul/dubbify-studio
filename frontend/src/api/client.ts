// src/api/client.ts
// Centralised API client — all backend calls go through here

import axios from 'axios'
import type {
  Project, ProjectCreate,
  Job, PipelineStartResponse,
  Speaker, SpeakerUpdate, SpeakerCreate,
  Segment, SegmentUpdate, SegmentCreate,
  TTSResponse, HealthResponse,
  Voice, VoiceMode,
} from '@/types'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
})

// Response interceptor — extract meaningful error message from FastAPI responses
api.interceptors.response.use(
  (r) => r,
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

  delete: (id: string) =>
    api.delete(`/projects/${id}`),
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
export const tts = {
  synthesizeSegment: (segmentId: string) =>
    api.post<TTSResponse>(`/tts/synthesize/segment/${segmentId}`).then((r) => r.data),
 
  synthesizeBatch: (segmentIds: string[]) =>
    api.post<{ results: any[] }>('/tts/synthesize/batch', { segment_ids: segmentIds }).then((r) => r.data),

  synthesizeJob: (jobId: string) =>
    api.post(`/tts/synthesize/job/${jobId}`).then((r) => r.data),

  mixFinalAudio: (jobId: string, muteOriginal = true) =>
    api.post(`/tts/mix/${jobId}`, null, {
      params: { mute_original: muteOriginal }
    }).then((r) => r.data),
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

  update: (id: string, data: Partial<Pick<Voice, 'name' | 'mode' | 'description' | 'reference_transcript' | 'cfg_value' | 'inference_timesteps'>>) =>
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
