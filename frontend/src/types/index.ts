// src/types/index.ts
// Central type definitions for Dubify Studio

// ── Status Enums ─────────────────────────────────────────────────

export type ProjectStatus = 'active' | 'archived' | 'draft'

export type JobStatus =
  | 'pending'
  | 'extracting'
  | 'separating'
  | 'stems_ready'
  | 'diarizing'
  | 'transcribing'
  | 'translating'
  | 'synthesizing'
  | 'mixing'
  | 'completed'
  | 'failed'

export type SegmentStatus = 'pending' | 'approved' | 'editing'

// ── Domain Models ─────────────────────────────────────────────────

export interface Project {
  id: string
  name: string
  description?: string
  source_lang: string
  target_lang: string
  status: ProjectStatus
  created_at: string
  updated_at: string
  job_count?: number
  logo_path?: string
  logo_url?: string | null
}

export interface ProjectUpdate {
  name?: string
  description?: string
}

export type OverlayType = 'image' | 'subtitle' | 'shape'

export interface Overlay {
  id: string
  job_id: string
  type: OverlayType
  media_path: string
  media_url?: string | null
  x: number       // fraction (0-1) of video width — top-left corner
  y: number       // fraction (0-1) of video height
  width: number   // fraction (0-1) of video width
  height: number  // fraction (0-1) of video height
  opacity: number
  z_index: number
  start_time?: number | null
  end_time?: number | null
  font_size: number         // subtitle overlays only
  color: string             // subtitle text fill, or shape fill color
  outline_color: string     // subtitle overlays only — text stroke
  background_color: string  // subtitle overlays only — box fill behind text; "" = none
  blur: boolean              // shape overlays only — blur the video underneath instead of filling with `color`
}

export interface OverlayUpdate {
  x?: number
  y?: number
  width?: number
  height?: number
  opacity?: number
  z_index?: number
  start_time?: number | null
  end_time?: number | null
  font_size?: number
  color?: string
  outline_color?: string
  background_color?: string
  blur?: boolean
}

// Workspace-global saved overlay layout ("brand kit") — snapshot of one
// episode's overlays, re-applicable to any other episode. When is_default,
// it's stamped automatically onto every newly uploaded episode.
export interface OverlayTemplate {
  id: string
  name: string
  is_default: boolean
  item_count: number
}

export interface ProjectCreate {
  name: string
  source_lang: string
  target_lang: string
}

export interface Job {
  id: string
  project_id: string
  status: JobStatus
  progress: number
  error_msg: string
  video_path: string
  audio_path: string
  output_path: string
  duration_secs: number
  // Computed by backend — HTTP URLs served via /uploads static mount
  video_url?: string | null
  output_url?: string | null
  created_at: string
  completed_at?: string | null
}

export interface SubtitleTrack {
  id: string
  job_id: string
  language: string
  label: string
  is_default: boolean
  track_index: number
}

export interface Speaker {
  id: string
  project_id: string
  label: string
  display_name: string
  color?: string
  voice_id?: string
  voice_design_prompt?: string
  gender?: string
  age_group?: string
  segment_count?: number
}

export interface SpeakerUpdate {
  display_name?: string
  color?: string
  voice_id?: string | null
  voice_design_prompt?: string
  gender?: string
  age_group?: string
}

export type VoiceMode = 'design' | 'clone' | 'ultimate'

export interface Voice {
  id: string
  name: string
  mode: VoiceMode
  description: string
  reference_audio_path: string
  reference_transcript: string
  cfg_value: number
  inference_timesteps: number
  seed: number
  created_at: string
  // computed by backend
  has_reference?: boolean
  reference_audio_url?: string | null
}

export interface Segment {
  id: string
  job_id: string
  speaker_id?: string | null
  voice_id?: string | null
  lane_index?: number | null  // timeline row — independent of speaker_id
  start_time: number      // seconds
  end_time: number        // seconds
  source_text: string     // original language (e.g. Chinese)
  english_text: string    // intermediate English
  khmer_text: string      // final translated text (target language)
  tts_audio_path: string  // absolute filesystem path (server-internal, not fetchable)
  tts_audio_url?: string | null  // /uploads/... URL — use this for playback/waveform fetches
  tts_duration_secs: number
  is_approved: boolean
  notes: string
  volume_db: number       // dB gain applied to the synthesised clip, 0 = unchanged
  voice_filter: string    // ffmpeg preset name ("" | "echo" | "synth" | "bass" | "phone")
  voice_speed: number     // atempo multiplier applied to the synthesised clip, 1.0 = unchanged
}

export interface SegmentUpdate {
  speaker_id?: string | null
  voice_id?: string | null
  lane_index?: number | null
  start_time?: number
  end_time?: number
  source_text?: string
  english_text?: string
  khmer_text?: string
  tts_audio_path?: string
  is_approved?: boolean
  notes?: string
  volume_db?: number
  voice_filter?: string
  voice_speed?: number
}

export interface SegmentCreate {
  speaker_id?: string | null
  voice_id?: string | null
  lane_index?: number | null
  start_time: number
  end_time: number
  source_text?: string
  english_text?: string
  khmer_text?: string
  notes?: string
}

export interface SpeakerCreate {
  label?: string
  display_name?: string
  gender?: string
  age_group?: string
  voice_design_prompt?: string
}

// ── API Responses ─────────────────────────────────────────────────

export interface PipelineStartResponse {
  job_id: string
  status: JobStatus
  message?: string
}

// Mirrors backend schemas.TTSResponse — the previous shape
// ({ audio_url, duration }) matched no field the API actually returns.
export interface TTSResponse {
  segment_id: string
  audio_path: string
  duration_secs: number
  success: boolean
  error: string
}

export interface HealthResponse {
  status: 'ok' | 'error'
  version?: string
  timestamp?: string
}

// ── Editor UI State ───────────────────────────────────────────────

export interface EditorState {
  currentTime: number
  duration: number
  isPlaying: boolean
  volume: number
  playbackRate: number
  zoom: number
  activeSegmentId: string | null
  editingSegmentId: string | null
  selectedSpeakerId: string | null
  leftPanelCollapsed: boolean
  rightPanelCollapsed: boolean
  timelineHeight: number
}

// ── UI Helpers ────────────────────────────────────────────────────

export type StatusColor = 'violet' | 'blue' | 'green' | 'yellow' | 'red' | 'gray'

export const JOB_STATUS_CONFIG: Record<JobStatus, {
  label: string
  color: StatusColor
  description: string
}> = {
  pending:      { label: 'Pending',      color: 'gray',   description: 'Waiting to start' },
  extracting:   { label: 'Extracting',   color: 'violet', description: 'Extracting audio' },
  separating:   { label: 'Separating',   color: 'violet', description: 'Splitting vocals from background music' },
  stems_ready:  { label: 'Stems Ready',  color: 'green',  description: 'Audio split — click Analyze to detect speakers' },
  diarizing:    { label: 'Diarizing',    color: 'violet', description: 'Detecting speakers' },
  transcribing: { label: 'Transcribing', color: 'violet', description: 'AI speech recognition' },
  translating:  { label: 'Translating',  color: 'blue',   description: 'Translating dialogue' },
  synthesizing: { label: 'Synthesizing', color: 'violet', description: 'Generating AI voices' },
  mixing:       { label: 'Mixing',       color: 'blue',   description: 'Mixing dubbed audio' },
  completed:    { label: 'Completed',    color: 'green',  description: 'Export ready' },
  failed:       { label: 'Failed',       color: 'red',    description: 'An error occurred' },
}

/** Safe lookup — returns a fallback config if an unknown status arrives from the API */
export function getJobStatusConfig(status: string) {
  return JOB_STATUS_CONFIG[status as JobStatus] ?? {
    label: status,
    color: 'gray' as StatusColor,
    description: 'Unknown status',
  }
}

export const PIPELINE_STEPS: { key: JobStatus; label: string; icon: string }[] = [
  { key: 'extracting',   label: 'Extract',   icon: 'audio-waveform' },
  { key: 'separating',   label: 'Separate',  icon: 'scissors' },
  { key: 'diarizing',    label: 'Speakers',  icon: 'users' },
  { key: 'transcribing', label: 'Transcribe', icon: 'file-text' },
  { key: 'translating',  label: 'Translate', icon: 'globe' },
  { key: 'synthesizing', label: 'Synthesize', icon: 'mic' },
  { key: 'mixing',       label: 'Mix',       icon: 'music' },
]

export const SPEAKER_COLORS = [
  '#7C3AED', // violet
  '#2563EB', // blue
  '#059669', // emerald
  '#D97706', // amber
  '#DC2626', // red
  '#DB2777', // pink
  '#0891B2', // cyan
  '#65A30D', // lime
]

export const LANGUAGE_OPTIONS = [
  { value: 'zh', label: 'Chinese' },
  { value: 'km', label: 'Khmer'   },
]
