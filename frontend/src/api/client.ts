// src/api/client.ts
// Centralised API client — all backend calls go through here

import axios, { type AxiosProgressEvent } from 'axios'
import type {
  MediaAsset, MediaClip, MediaClipList, MediaTemplate, MediaTrackFlagMap, Project, ProjectCreate, ProjectUpdate, Job, PipelineStartResponse, Speaker, SpeakerUpdate, SpeakerCreate, SpeakerMergeResponse, Segment, SegmentUpdate, SegmentCreate, LayoutChange, SplitResult, CharacterPicked, Stem, StemPiece, TTSResponse, HealthResponse, Voice, VoiceMode, VoiceGender, VoiceAgeGroup, Overlay, OverlayUpdate, OverlayTemplate, ChatMessage, ChatSendResponse, ChatProposedEdit, ChatThread, AppSettings, AppSettingsUpdate, GeminiModelChoice, GeminiModelSetting, GeminiModelTestResult, StoryEdit, StoryView, TrackFlagMap,
} from '@/types'
import type { StemPieceLists } from '@/lib/stemPieces'

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
export const appSettings = {
  get: () =>
    api.get<AppSettings>('/settings').then((r) => r.data),

  update: (data: AppSettingsUpdate) =>
    api.put<AppSettings>('/settings', data).then((r) => r.data),

  // The Gemini models the saved key can use, per job.
  models: () =>
    api.get<{ choices: GeminiModelChoice[] }>('/settings/models').then((r) => r.data.choices),

  // A tiny real request: a listed model can still refuse to work.
  testModel: (setting: GeminiModelSetting, model: string) =>
    api.post<GeminiModelTestResult>('/settings/models/test', { setting, model }).then((r) => r.data),
}

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
const uploadProgress = (onProgress?: (fraction: number) => void) =>
  onProgress ? (e: AxiosProgressEvent) => onProgress(e.total ? e.loaded / e.total : 0) : undefined

export const jobs = {
  /** `onProgress` gets the fraction of the file sent so far (0–1). */
  upload: (projectId: string, file: File, onProgress?: (fraction: number) => void) => {
    const form = new FormData()
    form.append('file', file)
    return api.post<PipelineStartResponse>(
      `/jobs/upload/${projectId}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 600_000, onUploadProgress: uploadProgress(onProgress) }
    ).then((r) => r.data)
  },

  uploadWithSubtitle: (projectId: string, video: File, subtitle: File, onProgress?: (fraction: number) => void) => {
    const form = new FormData()
    form.append('video', video)
    form.append('subtitle', subtitle)
    return api.post<PipelineStartResponse>(
      `/jobs/upload-subtitle/${projectId}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 600_000, onUploadProgress: uploadProgress(onProgress) }
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

  // Re-detect who speaks each existing segment; text, edits and voices are kept.
  detectSpeakers: (jobId: string) =>
    api.post(`/jobs/${jobId}/detect-speakers`).then((r) => r.data),

  // Translate lines that have no translation yet; translated and edited lines are kept.
  translateMissing: (jobId: string) =>
    api.post<{ lines: number }>(`/jobs/${jobId}/translate`).then((r) => r.data),

  // Separate music and voices again after the service failed; lines and voices are kept.
  retrySeparation: (jobId: string) =>
    api.post(`/jobs/${jobId}/separate`).then((r) => r.data),

  setBgmVolume: (jobId: string, volumeDb: number) =>
    api.patch<Job>(`/jobs/${jobId}/bgm-volume`, { volume_db: volumeDb }).then((r) => r.data),

  /** The music & effects dip under the voices (preview and export). */
  setDucking: (jobId: string, on: boolean) =>
    api.patch<Job>(`/jobs/${jobId}/ducking`, { duck_music: on }).then((r) => r.data),
  /** Every voice track's switches (lock, hide, mute), keyed by lane_index. */
  setTrackFlags: (jobId: string, flags: TrackFlagMap) =>
    api.put<Job>(`/jobs/${jobId}/track-flags`, { flags }).then((r) => r.data),

  frames: (jobId: string) =>
    api.get<PictureStrip>(`/jobs/${jobId}/frames`).then((r) => r.data),

  setTitle: (jobId: string, title: string) =>
    api.patch<Job>(`/jobs/${jobId}/title`, { title }).then((r) => r.data),

  setFinished: (jobId: string, finished: boolean) =>
    api.patch<Job>(`/jobs/${jobId}/finished`, { finished }).then((r) => r.data),
}

// ── Speakers ──────────────────────────────────────────────────
export const speakers = {
  listByProject: (projectId: string) =>
    api.get<Speaker[]>(`/projects/${projectId}/speakers`).then((r) => r.data),

  create: (projectId: string, data: SpeakerCreate) =>
    api.post<Speaker>(`/projects/${projectId}/speakers`, data).then((r) => r.data),

  update: (speakerId: string, data: SpeakerUpdate) =>
    api.patch<Speaker>(`/speakers/${speakerId}`, data).then((r) => r.data),

  /** Move all of a speaker's lines, in every episode, to another speaker and remove it. */
  merge: (speakerId: string, into: string) =>
    api.post<SpeakerMergeResponse>(`/speakers/${speakerId}/merge`, { into }).then((r) => r.data),

  /** Undo a merge into `speakerId` in one step: the removed speaker comes back as it was, with its lines. */
  unmerge: (speakerId: string, merged: SpeakerMergeResponse) =>
    api.post<Speaker>(`/speakers/${speakerId}/unmerge`, merged).then((r) => r.data),
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

  /** Move / trim / re-track several clips at once; returns every clip whose place changed. */
  /** `trackFlags`: the tracks' switches after a drop that inserted a track (they move with their tracks). */
  layout: (jobId: string, changes: LayoutChange[], trackFlags?: TrackFlagMap) =>
    api.put<{ segments: Segment[] }>(`/jobs/${jobId}/segments/layout`, trackFlags ? { changes, track_flags: trackFlags } : { changes })
      .then((r) => r.data.segments),

  split: (segmentId: string, at: number) =>
    api.post<SplitResult>(`/segments/${segmentId}/split`, { at }).then((r) => r.data),

  pickCharacter: (segmentId: string, speakerId: string) =>
    api.post<CharacterPicked>(`/segments/${segmentId}/character`, { speaker_id: speakerId }).then((r) => r.data),

  duplicate: (segmentId: string) =>
    api.post<Segment>(`/segments/${segmentId}/duplicate`).then((r) => r.data),

  approve: (segmentId: string) =>
    api.post<Segment>(`/segments/${segmentId}/approve`).then((r) => r.data),

  approveAll: (jobId: string) =>
    api.post<{ approved: number }>(`/jobs/${jobId}/approve-all`).then((r) => r.data),
}

// ── The music and the original voices in pieces (0.3.2) ──────
export const stemPieces = {
  /** Both stems' pieces; a stem nobody cut is one whole piece. */
  list: (jobId: string) =>
    api.get<StemPieceLists>(`/jobs/${jobId}/stem-pieces`).then((r) => r.data),
  /** Replace a stem's pieces (an edit or an undo); returns what was stored. */
  put: (jobId: string, stem: Stem, pieces: StemPiece[]) =>
    api.put<StemPiece[]>(`/jobs/${jobId}/stem-pieces/${stem}`, { pieces }).then((r) => r.data),
}

// ── Media (0.4.0): the project's library, an episode's media clips, templates ──
export const media = {
  list: (projectId: string) => api.get<MediaAsset[]>(`/projects/${projectId}/media`).then((r) => r.data),
  /** Add an image or a video; it's 'preparing' until its preview copy is made. */
  upload: (projectId: string, file: File, onProgress?: (fraction: number) => void) => {
    const form = new FormData()
    form.append('file', file)
    return api.post<MediaAsset>(`/projects/${projectId}/media`, form, {
      timeout: 0,
      onUploadProgress: (e: AxiosProgressEvent) => { if (onProgress && e.total) onProgress(e.loaded / e.total) },
    }).then((r) => r.data)
  },
  /** Remove media with its clips in every episode (and from templates). */
  remove: (assetId: string) =>
    api.delete<{ removed_clips: number; episodes: number }>(`/media/${assetId}`).then((r) => r.data),
  clips: (jobId: string) => api.get<MediaClipList>(`/jobs/${jobId}/media-clips`).then((r) => r.data),
  /** Replace the episode's media clips (an edit or an undo); returns what was stored. */
  setClips: (jobId: string, clips: MediaClip[], trackFlags?: MediaTrackFlagMap) =>
    api.put<MediaClipList>(`/jobs/${jobId}/media-clips`, trackFlags ? { clips, track_flags: trackFlags } : { clips })
      .then((r) => r.data),
  templates: (projectId: string) =>
    api.get<MediaTemplate[]>(`/projects/${projectId}/media-templates`).then((r) => r.data),
  saveTemplate: (projectId: string, jobId: string, name: string) =>
    api.post<MediaTemplate>(`/projects/${projectId}/media-templates`, { job_id: jobId, name }).then((r) => r.data),
  renameTemplate: (templateId: string, name: string) =>
    api.patch<MediaTemplate>(`/media-templates/${templateId}`, { name }).then((r) => r.data),
  removeTemplate: (templateId: string) => api.delete(`/media-templates/${templateId}`),
  /** Add a template's clips to an episode; returns the episode's new list and how many were skipped. */
  applyTemplate: (templateId: string, jobId: string) =>
    api.post<{ clips: MediaClip[]; skipped: number }>(`/media-templates/${templateId}/apply/${jobId}`).then((r) => r.data),
}

/** GET /jobs/{id}/frames — the picture strip, one frame every `interval` seconds from 0. */
export interface PictureStrip {
  ready: boolean
  interval: number
  urls: string[]
}

// ── TTS ───────────────────────────────────────────────────────
export interface BatchSynthesisStart {
  message: string
  batch_id: string
  started: number
  segment_ids: string[]
  skipped: string[]
}

/** GET /tts/synthesize/batch/{id} — `finished` once every line succeeded or failed for good. */
export interface BatchSynthesisStatus {
  total: number
  done: number
  failed: { segment_id: string; reason: string }[]
  backup_voice: number
  finished: boolean
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

  batchStatus: (batchId: string) =>
    api.get<BatchSynthesisStatus>(`/tts/synthesize/batch/${batchId}`).then((r) => r.data),

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
  // Speed up the voices that run into the next line (at most 1.25×, never slower):
  // how many it sped up, and how many still run into the next line.
  autofit: (jobId: string) =>
    api.post<{ fitted: number; left: number }>(
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
  gender?: VoiceGender | null
  age_group?: VoiceAgeGroup | null
  personality?: string[]
  roles?: string[]
}

function voiceFormData(data: Partial<VoiceCreateInput>): FormData {
  const form = new FormData()
  if (data.gender) form.append('gender', data.gender)
  if (data.age_group) form.append('age_group', data.age_group)
  data.personality?.forEach((tag) => form.append('personality', tag))   // one field per tag
  data.roles?.forEach((tag) => form.append('roles', tag))
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

  // Design-mode creation bakes a reference clip via TTS inline — Space
  // cold-start/queue can take minutes, so this must outlive the 30s default.
  create: (data: VoiceCreateInput) =>
    api.post<Voice>('/voices/', voiceFormData(data), {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 600_000,
    }).then((r) => r.data),

  // A null gender / age_group clears it (the voice goes back to My voices).
  update: (id: string, data: Partial<Pick<Voice, 'name' | 'mode' | 'description' | 'reference_transcript' | 'cfg_value' | 'inference_timesteps' | 'seed' | 'gender' | 'age_group' | 'personality' | 'roles'>>) =>
    api.patch<Voice>(`/voices/${id}`, data, { timeout: 600_000 }).then((r) => r.data),

  // One of the user's own voices made from any voice (built-in ones too), with its own clip.
  copy: (id: string) =>
    api.post<Voice>(`/voices/${id}/copy`).then((r) => r.data),

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

// ── Story notes ────────────────────────────────────────────────
export const story = {
  get: (projectId: string) =>
    api.get<StoryView>(`/projects/${projectId}/story`).then((r) => r.data),

  update: (projectId: string, edit: StoryEdit) =>
    api.patch<StoryView>(`/projects/${projectId}/story`, edit).then((r) => r.data),

  /** Learn from the finished episodes not read yet (also "Try again") */
  learn: (projectId: string) =>
    api.post<StoryView>(`/projects/${projectId}/story/learn`).then((r) => r.data),
}

// ── Assistant chat ────────────────────────────────────────────
export const chat = {
  /** The project's chats, the one used last first */
  listThreads: (projectId: string) =>
    api.get<ChatThread[]>(`/chat/${projectId}/threads`).then((r) => r.data),

  listMessages: (threadId: string) =>
    api.get<ChatMessage[]>(`/chat/threads/${threadId}/messages`).then((r) => r.data),

  deleteThread: (threadId: string) =>
    api.delete(`/chat/threads/${threadId}`),

  // A turn can involve several tool-call round trips to Gemini — give it
  // real headroom instead of the 30s default.
  /** `threadId`: the chat to continue (null starts one); `lineId`: the line the text cursor is in ("this line") */
  sendMessage: (projectId: string, { text, threadId, lineId }: { text: string; threadId: string | null; lineId?: string }) =>
    api.post<ChatSendResponse>(`/chat/${projectId}/messages`, { text, thread_id: threadId, line_id: lineId }, { timeout: 120_000 })
      .then((r) => r.data),

  listPendingEdits: (projectId: string, status: 'pending' | 'all' = 'pending') =>
    api.get<ChatProposedEdit[]>(`/chat/${projectId}/pending-edits`, { params: { status } }).then((r) => r.data),

  approveEdit: (editId: string) =>
    api.post<ChatProposedEdit>(`/chat/pending-edits/${editId}/approve`).then((r) => r.data),

  rejectEdit: (editId: string) =>
    api.post<ChatProposedEdit>(`/chat/pending-edits/${editId}/reject`).then((r) => r.data),
}

export default api
