// src/api/client.ts
// Centralised API client — all backend calls go through here

import axios from 'axios'
import type {
  Project, ProjectCreate,
  Job, PipelineStartResponse,
  Speaker, SpeakerUpdate,
  Segment, SegmentUpdate,
  TTSResponse, HealthResponse,
} from '@/types'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

// ── Health ────────────────────────────────────────────────────
export const health = {
  check: () =>
    api.get<HealthResponse>('/health').then(r => r.data),
}

// ── Projects ──────────────────────────────────────────────────
export const projects = {
  list: () =>
    api.get<Project[]>('/projects/').then(r => r.data),

  get: (id: string) =>
    api.get<Project>(`/projects/${id}`).then(r => r.data),

  create: (data: ProjectCreate) =>
    api.post<Project>('/projects/', data).then(r => r.data),

  delete: (id: string) =>
    api.delete(`/projects/${id}`),
}

// ── Jobs ──────────────────────────────────────────────────────
export const jobs = {
  // Upload video — auto-detects embedded subtitles
  upload: (projectId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.post<PipelineStartResponse>(
      `/jobs/upload/${projectId}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 600000 }
    ).then(r => r.data)
  },

  // Upload video + separate subtitle file
  uploadWithSubtitle: (projectId: string, video: File, subtitle: File) => {
    const form = new FormData()
    form.append('video', video)
    form.append('subtitle', subtitle)
    return api.post<PipelineStartResponse>(
      `/jobs/upload-subtitle/${projectId}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 600000 }
    ).then(r => r.data)
  },

  get: (jobId: string) =>
    api.get<Job>(`/jobs/${jobId}`).then(r => r.data),

  listByProject: (projectId: string) =>
    api.get<Job[]>(`/jobs/project/${projectId}`).then(r => r.data),

  delete: (jobId: string) =>
    api.delete(`/jobs/${jobId}`),

  getSubtitleTracks: (jobId: string) =>
    api.get(`/jobs/${jobId}/subtitle-tracks`).then(r => r.data),
}

// ── Speakers ──────────────────────────────────────────────────
export const speakers = {
  listByProject: (projectId: string) =>
    api.get<Speaker[]>(`/projects/${projectId}/speakers`).then(r => r.data),

  update: (speakerId: string, data: SpeakerUpdate) =>
    api.patch<Speaker>(`/speakers/${speakerId}`, data).then(r => r.data),
}

// ── Segments ──────────────────────────────────────────────────
export const segments = {
  listByJob: (jobId: string) =>
    api.get<Segment[]>(`/jobs/${jobId}/segments`).then(r => r.data),

  update: (segmentId: string, data: SegmentUpdate) =>
    api.patch<Segment>(`/segments/${segmentId}`, data).then(r => r.data),

  approve: (segmentId: string) =>
    api.post<Segment>(`/segments/${segmentId}/approve`).then(r => r.data),

  approveAll: (jobId: string) =>
    api.post(`/jobs/${jobId}/approve-all`).then(r => r.data),
}

// ── TTS ───────────────────────────────────────────────────────
export const tts = {
  synthesizeSegment: (segmentId: string) =>
    api.post<TTSResponse>(`/tts/synthesize/segment/${segmentId}`).then(r => r.data),

  synthesizeJob: (jobId: string) =>
    api.post(`/tts/synthesize/job/${jobId}`).then(r => r.data),

  mixFinalAudio: (jobId: string, muteOriginal = true) =>
    api.post(`/tts/mix/${jobId}`, null, {
      params: { mute_original: muteOriginal }
    }).then(r => r.data),
}

export default api
