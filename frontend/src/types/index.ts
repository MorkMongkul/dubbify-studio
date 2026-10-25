// src/types/index.ts
// Central type definitions for Movelo Studio

// ── Status Enums ─────────────────────────────────────────────────

export type ProjectStatus = 'active' | 'archived' | 'draft'

export type JobStatus =
  | 'pending'
  | 'extracting'
  | 'separating'
  | 'stems_ready'
  | 'diarizing'
  | 'transcribing'
  | 'recognizing'
  | 'translating'
  | 'synthesizing'
  | 'mixing'
  | 'completed'
  | 'failed'

export type SegmentStatus = 'pending' | 'approved' | 'editing'

// ── Domain Models ─────────────────────────────────────────────────

export type ChatEditMode = 'manual' | 'auto'

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
  chat_edit_mode: ChatEditMode
}

export interface ProjectUpdate {
  name?: string
  description?: string
  chat_edit_mode?: ChatEditMode
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
  /** Optional "About this drama"; it starts the project's story notes. */
  story_intro?: string
}

export interface Job {
  id: string
  project_id: string
  status: JobStatus
  progress: number
  error_msg: string
  title?: string
  video_path: string
  audio_path: string
  output_path: string
  duration_secs: number
  bgm_volume_db: number
  // Computed by backend — HTTP URLs served via /uploads static mount
  video_url?: string | null
  output_url?: string | null
  // null until the music/voices tracks exist; false when separation failed
  // and the original audio stands in for both
  stems_separated?: boolean | null
  // changes when the tracks are made again — appended to their URLs
  stems_version?: number
  // how long the running step has been going (null when unknown), for the progress ring's estimate
  step_elapsed_secs?: number | null
  created_at: string
  completed_at?: string | null
  /** Marked finished by the user (its translation is right); the story notes learn from it. */
  finished_at?: string | null
  /** The video's frame rate (one ← / → step); null until read. */
  frame_rate?: number | null
  /** The music & effects dip under the voices (off for episodes analysed before 0.3.1). */
  duck_music?: boolean
  /** Each voice track's switches (lock, hide, mute), keyed by lane_index; only tracks with one on. */
  track_flags?: TrackFlagMap
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
  /** The app picked voice_id because nobody chose one (backend auto_voice.py). */
  voice_auto?: boolean
  /** A thumbnail of the speaker's face, when one was made. */
  face_crop_url?: string | null
  voice_design_prompt?: string
  gender?: string
  age_group?: string
  segment_count?: number
}

/** POST /speakers/{id}/merge — the same person under two labels (backend routes/segments.py merge_speaker). */
export interface SpeakerMergeResponse {
  /** Lines that changed speaker */
  moved: string[]
  /** The merged-away speaker as it was — POST /speakers/{into}/unmerge creates it again */
  removed: Speaker
  /** Story characters whose speaker link moved */
  relinked: string[]
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

/** Who a voice sounds like, and roughly how old (backend app/services/voice_tags.py). */
export type VoiceGender = 'man' | 'woman' | 'boy' | 'girl'
export type VoiceAgeGroup = 'child' | 'teen' | '20s' | '30s' | '40s' | '50s' | '60s' | '70s+'
/** Voices-tab section; the backend derives it from gender, age and roles. */
export type VoiceSectionKey =
  'young_men' | 'young_women' | 'older_men' | 'older_women' | 'elders' | 'teens_children' | 'narrators'

/** The tags a person edits on one of their own voices. */
export interface VoiceTagValue {
  gender: VoiceGender | null
  age_group: VoiceAgeGroup | null
  personality: string[]
  roles: string[]
}

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
  /** Ships with the app: can't be renamed, edited or deleted. */
  builtin?: boolean
  gender?: VoiceGender | null
  age_group?: VoiceAgeGroup | null
  personality?: string[]
  roles?: string[]
  /** null = "My voices": no "who" chosen yet */
  section?: VoiceSectionKey | null
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
  /** Where the actor spoke in the original; null for a line added by hand. */
  source_start?: number | null
  source_end?: number | null
  /** The clip plays its voice file from voice_offset for (end − start) s at fit_rate. */
  voice_offset: number
  fit_rate: number
  /** The Khmer text the voice was made from (differs from khmer_text once edited). */
  voice_text?: string | null
  /** Linear fades on the clip as trimmed, in seconds. */
  fade_in_secs: number
  fade_out_secs: number
  /** The two speakers Movelo can't choose between for this line; null = sure. */
  who_check?: WhoGuess[] | null
  /** The user picked the speaker. */
  who_confirmed?: boolean
}

export interface WhoGuess {
  speaker_id: string
  /** What the guess rests on. */
  basis: string
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
  source_start?: number | null
  source_end?: number | null
  voice_offset?: number
  voice_text?: string | null
  fade_in_secs?: number
  fade_out_secs?: number
  who_check?: WhoGuess[] | null
  who_confirmed?: boolean
  /** Only undo sends it: puts a re-translated line's machine Khmer back, so it still counts as unedited. */
  khmer_machine?: string | null
}

/** GET /projects/{id}/character-suggestions: two characters (a < b) that may be one. Only the user's click merges. */
export interface CharacterSuggestion {
  a: string
  b: string
  reason: 'same voice' | 'same voice and face'
}

/** POST /segments/{id}/character: the pick, and whether the line's Khmer was translated again for the new speaker. */
export interface CharacterPicked {
  segment: Segment
  retranslated: boolean
  /** The Khmer before the pick, even when nothing changed. */
  previous_khmer: string
}

/** One clip's new place on the timeline (PUT /jobs/{id}/segments/layout). */
export type { LayoutChange } from '@/lib/tracks'
import type { TrackFlagMap } from '@/lib/trackFlags'
export type { TrackFlagMap } from '@/lib/trackFlags'

/** POST /segments/{id}/split: the original (shortened) and the new second half. */
export interface SplitResult {
  first: Segment
  second: Segment
}

/** A stem of the episode that can be cut into pieces (0.3.2). */
export type Stem = 'music' | 'voices'

/** The part of a stem heard from start to end, at the same time as in the video (never moves). */
export interface StemPiece {
  id: string
  stem: Stem
  start_time: number
  end_time: number
  volume_db: number
  fade_in_secs: number
  fade_out_secs: number
  /** Original voices only: this piece is in the export (drawn in gold). Music pieces always are. */
  in_export: boolean
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
  /** null: the app picks a fitting built-in voice the first time it's voiced */
  voice_id?: string | null
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
  engine?: string
  // Gemini's stock voice stood in because the voice server didn't respond
  backup_voice?: boolean
}

export interface HealthResponse {
  status: 'ok' | 'error'
  version?: string
  services?: Record<string, string>
}

// ── App settings (desktop Settings panel) ──────────────────────────
// Secrets are write-only: the API only says whether each one is set.

export interface AppSettingsValues {
  VOXCPM2_API_URL: string
  VOXCPM2_HF_SPACE_FALLBACK: string
  TRANSLATION_BACKEND: 'gemini' | 'deep'
  GEMINI_MODEL: string
  GEMINI_ASR_MODEL: string
  GEMINI_CHAT_MODEL: string
  GEMINI_TTS_MODEL: string
  TTS_BATCH_CONCURRENCY: number
}

export interface AppSettings {
  /** False during development, where settings come from backend/.env. */
  editable: boolean
  values: AppSettingsValues
  secrets: { GEMINI_API_KEY: boolean; HF_TOKEN: boolean }
  storage: { data_dir: string; upload_dir: string; database: 'local' | 'server'; log_dir: string }
  ffmpeg: string | null
  ffprobe: string | null
  version: string
}

/** Only the fields sent change; an empty secret removes it. */
export type AppSettingsUpdate = Partial<AppSettingsValues & { GEMINI_API_KEY: string; HF_TOKEN: string }>

export type GeminiModelSetting = 'GEMINI_MODEL' | 'GEMINI_ASR_MODEL' | 'GEMINI_CHAT_MODEL' | 'GEMINI_TTS_MODEL'

/** One AI job's model choice in Settings. An empty `value` means Automatic. */
export interface GeminiModelChoice {
  setting: GeminiModelSetting
  label: string
  value: string
  /** The model Automatic stands for. */
  automatic: string
  /** The model actually answering — differs when a retired one was replaced. */
  in_use: string
  /** Models this key can see that suit this job: Google's aliases, then newest first. */
  options: string[]
}

export interface GeminiModelTestResult {
  ok: boolean
  message: string
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

export type StatusColor = 'brand' | 'blue' | 'green' | 'yellow' | 'red' | 'gray'

export const JOB_STATUS_CONFIG: Record<JobStatus, {
  label: string
  color: StatusColor
  description: string
}> = {
  pending:      { label: 'Pending',      color: 'gray',   description: 'Waiting to start' },
  extracting:   { label: 'Extracting',   color: 'brand', description: 'Extracting audio' },
  separating:   { label: 'Separating',   color: 'brand', description: 'Splitting vocals from background music' },
  stems_ready:  { label: 'Stems Ready',  color: 'green',  description: 'Audio split — click Analyze to detect speakers' },
  diarizing:    { label: 'Diarizing',    color: 'brand', description: 'Detecting speakers' },
  transcribing: { label: 'Transcribing', color: 'brand', description: 'AI speech recognition' },
  recognizing:  { label: 'Recognising',  color: 'brand', description: 'Recognising characters' },
  translating:  { label: 'Translating',  color: 'blue',   description: 'Translating dialogue' },
  synthesizing: { label: 'Synthesizing', color: 'brand', description: 'Generating AI voices' },
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

// Coral is the app's selection colour, so the common first speakers get
// colours far from it; pink and red come last.
export const SPEAKER_COLORS = [
  '#2563EB', // blue
  '#059669', // emerald
  '#D97706', // amber
  '#0891B2', // cyan
  '#7C3AED', // violet
  '#65A30D', // lime
  '#DB2777', // pink
  '#DC2626', // red
]

export const LANGUAGE_OPTIONS = [
  { value: 'zh', label: 'Chinese' },
  { value: 'km', label: 'Khmer'   },
]

// ── Assistant chat ─────────────────────────────────────────────────

export type ChatRole = 'user' | 'model'

export interface ChatFunctionCall {
  name: string
  args: Record<string, unknown>
}

export interface ChatFunctionResponse {
  name: string
  response: Record<string, unknown>
}

/** The line a message is about — where the text cursor was when it was sent (a snapshot). */
export interface ChatLineRef {
  segment_id: string
  episode: number
  time: number
  speaker: string
  speaker_tag: string
  source_text: string
  khmer_text: string
}

export interface ChatMessage {
  id: string
  project_id: string
  role: ChatRole
  text: string
  function_call?: ChatFunctionCall | null
  function_response?: ChatFunctionResponse | null
  line_ref?: ChatLineRef | null
  thread_id?: string | null
  created_at: string
}

export interface ChatSendResponse {
  /** The chat the turn went into — a new one when none was given */
  thread_id: string
  messages: ChatMessage[]
}

/** One of a project's Assistant chats, named after its first message */
export interface ChatThread {
  id: string
  project_id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export type ChatEditStatus = 'pending' | 'approved' | 'rejected'

/** Where a suggested edit's line is (null when the line was deleted since) */
export interface ChatEditLine {
  job_id: string
  episode: number
  time: number
  source_text: string
}

export interface ChatProposedEdit {
  id: string
  project_id: string
  segment_id: string
  message_id?: string | null
  old_khmer_text: string
  new_khmer_text: string
  reason: string
  status: ChatEditStatus
  created_at: string
  resolved_at?: string | null
  line?: ChatEditLine | null
}

// ── Story notes ────────────────────────────────────────────────
/** Who wrote a note: the user, or the AI from finished episodes. Learning never changes the user's. */
export type StoryBy = 'you' | 'ai'

export interface StoryCharacter {
  id: string
  zh: string
  km: string
  about: string
  speaker: string
  by: StoryBy
}

export interface StoryWord {
  id: string
  zh: string
  km: string
  note: string
  by: StoryBy
}

export interface StoryEpisode {
  job_id: string
  number: number
  title: string
  finished: boolean
  learned: boolean
  summary: string
  summary_by: StoryBy
}

export interface StoryLearning {
  state: 'idle' | 'running' | 'failed'
  message: string
}

export interface StoryView {
  intro: string
  tone: { text: string; by: StoryBy }
  characters: StoryCharacter[]
  words: StoryWord[]
  episodes: StoryEpisode[]
  learning: StoryLearning
  /** A Gemini key is set */
  can_learn: boolean
}

/** What the user changed, one row at a time: a row with an id changes it, without one adds it. */
export interface StoryEdit {
  intro?: string
  tone?: string
  character?: { id?: string; zh: string; km: string; about: string }
  remove_character?: string
  word?: { id?: string; zh: string; km: string; note: string }
  remove_word?: string
  summaries?: Record<string, string>
}

// ── Media (0.4.0): the Media tab's library, an episode's media clips, templates ──
export interface MediaBox { x: number; y: number; width: number; height: number }

export interface MediaAsset {
  id: string
  project_id: string
  name: string
  kind: 'image' | 'video'
  width: number
  height: number
  duration_secs: number | null
  has_alpha: boolean
  /** False where the transparency can't be kept (WebM, HEVC with alpha): its tile says so. */
  alpha_kept: boolean
  has_audio: boolean
  /** Where it was last placed: a new clip of it starts there. */
  last_box: MediaBox | null
  status: 'preparing' | 'ready' | 'failed'
  error: string | null
  created_at: string | null
  /** How many episodes have clips of it. */
  used_in: number
  url: string | null
  /** What the editor shows: a video's preview copy (colour over its alpha matte), an image itself. */
  preview_url: string | null
  thumb_url: string | null
  audio_url: string | null
}

export interface MediaClip extends MediaBox {
  id: string
  asset_id: string
  lane_index: number
  start_time: number
  end_time: number
  source_in: number
  volume_db: number
}

export interface MediaTrackSwitches { locked: boolean; hidden: boolean; muted: boolean }
/** media lane → switches; only tracks with a switch on. */
export type MediaTrackFlagMap = Record<string, MediaTrackSwitches>

export interface MediaClipList {
  clips: MediaClip[]
  track_flags: MediaTrackFlagMap
  /** The episode still has overlays from the old Elements tab (no longer exported). */
  old_overlays: boolean
}

export interface MediaTemplate {
  id: string
  project_id: string
  name: string
  items: Omit<MediaClip, 'id'>[]
  created_at: string | null
}
