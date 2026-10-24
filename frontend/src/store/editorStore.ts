// src/store/editorStore.ts
// Zustand store — UI-only editor state (no API data here)

import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'
import type { JobAction } from '@/lib/jobProgress'
import type { SegmentOverlay } from '@/lib/segmentOverlay'
import type { LayoutChange } from '@/lib/tracks'

// Which panel the left icon rail has open in the dynamic dock. Settings is a
// window of its own (settingsOpen), not a panel.
export type RailTab = 'projects' | 'speakers' | 'elements' | 'voices' | 'story' | 'assistant'

export interface VoicePreset {
  id: string
  name: string // e.g., "Main Character (Male)", "Narrator (Female)"
  gender: 'male' | 'female'
  reference_audio_path?: string // Path to voice clone reference file
}

interface EditorStore {
  // Playback
  currentTime: number
  duration: number
  isPlaying: boolean
  volume: number
  playbackRate: number

  // Timeline
  zoom: number
  timelineScrollLeft: number
  timelineHeight: number
  // Active timeline tool — 'select' drags/trims clips, 'blade' splits the clip
  // at the click position (CapCut-style).
  activeTool: 'select' | 'blade'
  // Magnetic snapping of clip edges to other clips + the playhead
  snapEnabled: boolean

  // Selection / active
  activeSegmentId: string | null
  selectedOverlayId: string | null
  editingSegmentId: string | null
  // The line whose text the cursor was last in: "this line" for the Assistant.
  // Follows the text cursor, not the mouse (activeSegmentId follows hover).
  assistantLineId: string | null
  selectedSpeakerId: string | null
  selectedSegmentIds: string[]
  // Segment ids queued in a running batch voice generation, plus the job
  // they belong to. Lives OUTSIDE DEFAULT_STATE (like railTab): the backend
  // batch keeps running wherever the user navigates, and resetEditor() fires
  // on every session switch — these must survive it so badges/banner and
  // polling resume when the user returns to that episode.
  batchGeneratingIds: string[]
  batchGeneratingJobId: string | null
  // The backend's id for the running batch (GET /tts/synthesize/batch/{id})
  batchId: string | null
  // What the user last started on each episode (by job id), so its progress
  // can say "Detecting speakers…" rather than guess from the status. Outside
  // DEFAULT_STATE too: the step keeps running across session switches.
  jobActions: Record<string, JobAction>
  setJobAction: (jobId: string, action: JobAction) => void
  // The Assistant chat open in each project (by project id): a chat's id,
  // null for a new chat, absent for "the one used last". Outside
  // DEFAULT_STATE: switching episodes keeps the chat.
  assistantThreads: Record<string, string | null>
  setAssistantThread: (projectId: string, threadId: string | null | undefined) => void
  availableVoices: VoicePreset[]
  inspectorMode: 'global_synthesis' | 'audio_clip_settings' | 'subtitle_settings'
  focusedTimelineItemId: string | null

  // Edits still in flight, laid over the server's segments (lib/segmentOverlay):
  // a drop or trim waiting for the server, text typed but not saved. Each entry
  // is dropped once the server answers — server state owns the segment again.
  segmentPositions: Record<string, SegmentOverlay>
  /** Show a drop / trim at once, ahead of the server. */
  setPendingLayout: (changes: LayoutChange[]) => void
  /** The server answered (or failed): its segments show again. Typed text stays. */
  clearPendingLayout: (ids: string[]) => void
  updateSegmentText: (id: string, text: string) => void
  clearSegmentTextOverride: (id: string) => void
  // A volume line being dragged: the new dB of each clip it moves (the dragged
  // one, or the whole selection it belongs to); the preview plays and the
  // timeline draws these until the save lands (then null). Targets, not a
  // change, so a refetch of already-saved clips never applies it twice.
  liveClipVolume: Record<string, number> | null
  setLiveClipVolume: (v: Record<string, number> | null) => void
  // Hear the mix in short snippets while dragging the playhead. A preference:
  // outside DEFAULT_STATE, so switching episodes keeps it.
  scrubAudio: boolean
  toggleScrubAudio: () => void
  // The selected piece of the music or the original voices (0.3.2)
  selectedPiece: { stem: 'music' | 'voices'; id: string } | null
  setSelectedPiece: (p: { stem: 'music' | 'voices'; id: string } | null) => void
  // A piece's volume line being dragged: the preview plays this level until saved
  livePieceVolume: { id: string; db: number } | null
  setLivePieceVolume: (v: { id: string; db: number } | null) => void

  // Mute & Solo States
  mutedTrackIds: Record<string, boolean>
  soloedTrackIds: Record<string, boolean>

  toggleMuteTrack: (speakerId: string | null) => void
  toggleSoloTrack: (speakerId: string | null) => void

  // Left icon rail → dynamic dock panel. Lives OUTSIDE DEFAULT_STATE on
  // purpose: resetEditor() fires on every session switch, and the user's
  // chosen tab (e.g. Voices) must survive it — EditorPage re-derives the tab
  // on real context changes instead.
  railTab: RailTab
  setRailTab: (t: RailTab) => void
  // The Settings window (the rail's gear, or ⌘,). Outside DEFAULT_STATE too.
  settingsOpen: boolean
  setSettingsOpen: (open: boolean) => void

  // Panel visibility
  leftPanelCollapsed: boolean
  rightPanelCollapsed: boolean

  // Actions — playback
  setCurrentTime: (t: number) => void
  setDuration: (d: number) => void
  setPlaying: (p: boolean) => void
  togglePlaying: () => void
  setVolume: (v: number) => void
  setPlaybackRate: (r: number) => void

  // Actions — timeline
  setZoom: (z: number) => void
  zoomIn: () => void
  zoomOut: () => void
  resetZoom: () => void
  setTimelineScrollLeft: (x: number) => void
  setTimelineHeight: (h: number) => void
  setActiveTool: (t: 'select' | 'blade') => void
  toggleSnap: () => void
  speakerPanelWidth: number
  setSpeakerPanelWidth: (w: number) => void

  // Actions — selection
  setActiveSegment: (id: string | null) => void
  setSelectedOverlay: (id: string | null) => void
  setEditingSegment: (id: string | null) => void
  setAssistantLineId: (id: string | null) => void
  setSelectedSpeaker: (id: string | null) => void
  toggleSelectSegment: (id: string) => void
  /** The clip last picked with a plain click on the timeline: a timeline Shift+click starts
   *  a selection from it while it's still the active clip (TimelineEditor.handleShiftSelect). */
  selectionAnchorId: string | null
  setSelectionAnchor: (id: string | null) => void
  toggleSelectAllSegments: (ids: string[]) => void
  /** Replace the multi-selection wholesale — used by the timeline marquee. */
  setSelectedSegments: (ids: string[]) => void
  setInspectorMode: (mode: 'global_synthesis' | 'audio_clip_settings' | 'subtitle_settings') => void
  setFocusedTimelineItemId: (id: string | null) => void

  // Actions — panels
  toggleLeftPanel: () => void
  toggleRightPanel: () => void
  setLeftPanelCollapsed: (v: boolean) => void
  setRightPanelCollapsed: (v: boolean) => void

  // Reset
  resetEditor: () => void
}

const DEFAULT_STATE = {
  currentTime: 0,
  duration: 0,
  isPlaying: false,
  volume: 1,
  playbackRate: 1,
  zoom: 1,
  timelineScrollLeft: 0,
  // Taller default than the old 300 so the same number of lanes stay visible
  // now that rows are 64px instead of 48px.
  timelineHeight: 380,
  activeTool: 'select' as 'select' | 'blade',
  snapEnabled: true,
  speakerPanelWidth: 228,
  activeSegmentId: null,
  selectedOverlayId: null,
  editingSegmentId: null,
  assistantLineId: null as string | null,
  selectedSpeakerId: null,
  leftPanelCollapsed: false,
  rightPanelCollapsed: false,
  segmentPositions: {},
  liveClipVolume: null as Record<string, number> | null,
  selectedPiece: null as { stem: 'music' | 'voices'; id: string } | null,
  livePieceVolume: null as { id: string; db: number } | null,
  mutedTrackIds: {},
  soloedTrackIds: {},
  selectedSegmentIds: [] as string[],
  selectionAnchorId: null as string | null,
  availableVoices: [
    { id: 'male_actor_1', name: 'Male Actor 1', gender: 'male' },
    { id: 'female_actor_1', name: 'Female Actor 1', gender: 'female' },
    { id: 'child_voice_1', name: 'Child Voice', gender: 'female' },
  ] as VoicePreset[],
  inspectorMode: 'global_synthesis' as const,
  focusedTimelineItemId: null as string | null,
}

export const useEditorStore = create<EditorStore>()(
  subscribeWithSelector((set, get) => ({
    ...DEFAULT_STATE,

    // ── Rail (not part of DEFAULT_STATE — see interface note) ──
    railTab: 'projects' as RailTab,
    setRailTab: (t) => {
      if (get().railTab !== t) set({ railTab: t })
    },
    settingsOpen: false,
    setSettingsOpen: (open) => set({ settingsOpen: open }),
    scrubAudio: true,
    toggleScrubAudio: () => set((s) => ({ scrubAudio: !s.scrubAudio })),
    setLiveClipVolume: (v) => set({ liveClipVolume: v }),
    setSelectedPiece: (p) => set({ selectedPiece: p }),
    setLivePieceVolume: (v) => set({ livePieceVolume: v }),

    // ── Batch voice generation (not part of DEFAULT_STATE) ─────
    // The backend batch keeps running no matter where the user navigates;
    // resetEditor() fires on every session switch, so keeping these outside
    // DEFAULT_STATE is what lets the progress tracking survive a hop to
    // another project and greet the user with live badges on return.
    batchGeneratingIds: [] as string[],
    batchGeneratingJobId: null as string | null,
    batchId: null as string | null,

    // ── Started actions (not part of DEFAULT_STATE — see interface note) ──
    jobActions: {} as Record<string, JobAction>,
    setJobAction: (jobId, action) => set((s) => ({ jobActions: { ...s.jobActions, [jobId]: action } })),
    assistantThreads: {} as Record<string, string | null>,
    setAssistantThread: (projectId, threadId) => set((s) => {
      const next = { ...s.assistantThreads }
      if (threadId === undefined) delete next[projectId]
      else next[projectId] = threadId
      return { assistantThreads: next }
    }),

    // ── Playback ──────────────────────────────────────────────
    setCurrentTime: (t) => {
      if (get().currentTime !== t) set({ currentTime: t })
    },
    setDuration: (d) => {
      if (get().duration !== d) set({ duration: d })
    },
    setPlaying: (p) => {
      if (get().isPlaying !== p) set({ isPlaying: p })
    },
    togglePlaying: () => set((s) => ({ isPlaying: !s.isPlaying })),
    setVolume: (v) => {
      const target = Math.max(0, Math.min(1, v))
      if (get().volume !== target) set({ volume: target })
    },
    setPlaybackRate: (r) => {
      if (get().playbackRate !== r) set({ playbackRate: r })
    },

    // ── Timeline ──────────────────────────────────────────────
    setZoom: (z) => set({ zoom: Math.max(0.25, Math.min(8, z)) }),
    zoomIn: () => set((s) => ({ zoom: Math.min(8, s.zoom * 1.25) })),
    zoomOut: () => set((s) => ({ zoom: Math.max(0.25, s.zoom * 0.8) })),
    resetZoom: () => set({ zoom: 1 }),
    setTimelineScrollLeft: (x) => set({ timelineScrollLeft: x }),
    setTimelineHeight: (h) => set({ timelineHeight: Math.max(220, Math.min(560, h)) }),
    setActiveTool: (t) => set({ activeTool: t }),
    toggleSnap: () => set((s) => ({ snapEnabled: !s.snapEnabled })),
    setSpeakerPanelWidth: (w) => set({ speakerPanelWidth: Math.max(160, Math.min(300, w)) }),

    // ── Selection ─────────────────────────────────────────────
    setActiveSegment: (id) => {
      const current = get().activeSegmentId
      if (current !== id) set({ activeSegmentId: id })
    },
    setSelectedOverlay: (id) => set({ selectedOverlayId: id }),
    setEditingSegment: (id) => set({ editingSegmentId: id }),
    setAssistantLineId: (id) => set({ assistantLineId: id }),
    setSelectedSpeaker: (id) => set({ selectedSpeakerId: id }),
    toggleSelectSegment: (id) => set((s) => ({
      selectedSegmentIds: s.selectedSegmentIds.includes(id)
        ? s.selectedSegmentIds.filter((x) => x !== id)
        : [...s.selectedSegmentIds, id]
    })),
    setSelectionAnchor: (id) => set({ selectionAnchorId: id }),
    toggleSelectAllSegments: (ids) => set((s) => {
      const allSelected = ids.every((id) => s.selectedSegmentIds.includes(id))
      return {
        selectedSegmentIds: allSelected
          ? s.selectedSegmentIds.filter((id) => !ids.includes(id))
          : Array.from(new Set([...s.selectedSegmentIds, ...ids]))
      }
    }),
    // Selecting clips takes the editing keys back from a selected piece
    setSelectedSegments: (ids) => set(ids.length ? { selectedSegmentIds: ids, selectedPiece: null } : { selectedSegmentIds: ids }),
    setInspectorMode: (mode) => set({ inspectorMode: mode }),
    setFocusedTimelineItemId: (id) => set({ focusedTimelineItemId: id }),

    setPendingLayout: (changes) =>
      set((s) => {
        const next = { ...s.segmentPositions }
        for (const { id, ...fields } of changes) next[id] = { ...next[id], ...fields }
        return { segmentPositions: next }
      }),
    clearPendingLayout: (ids) =>
      set((s) => {
        let changed = false
        const next = { ...s.segmentPositions }
        for (const id of ids) {
          const existing = next[id]
          if (!existing) continue
          changed = true
          if (existing.khmer_text === undefined) delete next[id]
          else next[id] = { khmer_text: existing.khmer_text }
        }
        return changed ? { segmentPositions: next } : s
      }),
    updateSegmentText: (id, text) =>
      set((s) => ({
        segmentPositions: { ...s.segmentPositions, [id]: { ...s.segmentPositions[id], khmer_text: text } },
      })),
    // Drop the local text override once the edit is persisted, handing the
    // segment's text back to server state. Without this the override outlives
    // the edit and keeps winning in EditorPage's merge — so undoing a text
    // change updated the server but the old text never reappeared on screen.
    clearSegmentTextOverride: (id) =>
      set((s) => {
        const existing = s.segmentPositions[id]
        if (!existing || existing.khmer_text === undefined) return s
        const next = { ...existing }
        delete next.khmer_text
        const all = { ...s.segmentPositions, [id]: next }
        if (Object.keys(next).length === 0) delete all[id]
        return { segmentPositions: all }
      }),

    toggleMuteTrack: (speakerId) => {
      const key = speakerId ?? '__none__'
      set((s) => ({
        mutedTrackIds: {
          ...s.mutedTrackIds,
          [key]: !s.mutedTrackIds[key],
        },
      }))
    },
    toggleSoloTrack: (speakerId) => {
      const key = speakerId ?? '__none__'
      set((s) => ({
        soloedTrackIds: {
          ...s.soloedTrackIds,
          [key]: !s.soloedTrackIds[key],
        },
      }))
    },
    // ── Panels ────────────────────────────────────────────────
    toggleLeftPanel: () => set((s) => ({ leftPanelCollapsed: !s.leftPanelCollapsed })),
    toggleRightPanel: () => set((s) => ({ rightPanelCollapsed: !s.rightPanelCollapsed })),
    setLeftPanelCollapsed: (v) => set({ leftPanelCollapsed: v }),
    setRightPanelCollapsed: (v) => set({ rightPanelCollapsed: v }),

    // ── Reset ─────────────────────────────────────────────────
    resetEditor: () => set(DEFAULT_STATE),
  }))
)

// Selector helpers for performance
export const useCurrentTime = () => useEditorStore((s) => s.currentTime)
export const useIsPlaying = () => useEditorStore((s) => s.isPlaying)
export const useDuration = () => useEditorStore((s) => s.duration)
export const useZoom = () => useEditorStore((s) => s.zoom)
export const useActiveSegmentId = () => useEditorStore((s) => s.activeSegmentId)
export const useEditingSegmentId = () => useEditorStore((s) => s.editingSegmentId)
export const useSelectedSegmentIds = () => useEditorStore((s) => s.selectedSegmentIds)
export const useAvailableVoices = () => useEditorStore((s) => s.availableVoices)
export const useInspectorMode = () => useEditorStore((s) => s.inspectorMode)
export const useFocusedTimelineItemId = () => useEditorStore((s) => s.focusedTimelineItemId)
