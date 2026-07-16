// src/store/editorStore.ts
// Zustand store — UI-only editor state (no API data here)

import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'

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

  // Selection / active
  activeSegmentId: string | null
  editingSegmentId: string | null
  selectedSpeakerId: string | null
  selectedSegmentIds: string[]
  availableVoices: VoicePreset[]
  inspectorMode: 'global_synthesis' | 'audio_clip_settings' | 'subtitle_settings'
  focusedTimelineItemId: string | null

  // Optimistic positions
  segmentPositions: Record<string, {
    start_time: number
    end_time: number
    lane_index: number
    tts_duration_secs?: number
    tts_audio_path?: string
    khmer_text?: string
  }>
  updateSegmentPosition: (
    id: string,
    start_time: number,
    end_time: number,
    lane_index: number,
    tts_duration_secs?: number,
    tts_audio_path?: string
  ) => void
  updateSegmentText: (id: string, text: string) => void
  clearSegmentPositions: () => void

  // Mute & Solo States
  mutedTrackIds: Record<string, boolean>
  soloedTrackIds: Record<string, boolean>

  toggleMuteTrack: (speakerId: string | null) => void
  toggleSoloTrack: (speakerId: string | null) => void

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
  speakerPanelWidth: number
  setSpeakerPanelWidth: (w: number) => void

  // Actions — selection
  setActiveSegment: (id: string | null) => void
  setEditingSegment: (id: string | null) => void
  setSelectedSpeaker: (id: string | null) => void
  toggleSelectSegment: (id: string) => void
  toggleSelectAllSegments: (ids: string[]) => void
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
  timelineHeight: 300,
  speakerPanelWidth: 228,
  activeSegmentId: null,
  editingSegmentId: null,
  selectedSpeakerId: null,
  leftPanelCollapsed: false,
  rightPanelCollapsed: false,
  segmentPositions: {},
  mutedTrackIds: {},
  soloedTrackIds: {},
  selectedSegmentIds: [] as string[],
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
    setTimelineHeight: (h) => set({ timelineHeight: Math.max(200, Math.min(500, h)) }),
    setSpeakerPanelWidth: (w) => set({ speakerPanelWidth: Math.max(160, Math.min(300, w)) }),

    // ── Selection ─────────────────────────────────────────────
    setActiveSegment: (id) => {
      const current = get().activeSegmentId
      if (current !== id) set({ activeSegmentId: id })
    },
    setEditingSegment: (id) => set({ editingSegmentId: id }),
    setSelectedSpeaker: (id) => set({ selectedSpeakerId: id }),
    toggleSelectSegment: (id) => set((s) => ({
      selectedSegmentIds: s.selectedSegmentIds.includes(id)
        ? s.selectedSegmentIds.filter((x) => x !== id)
        : [...s.selectedSegmentIds, id]
    })),
    toggleSelectAllSegments: (ids) => set((s) => {
      const allSelected = ids.every((id) => s.selectedSegmentIds.includes(id))
      return {
        selectedSegmentIds: allSelected
          ? s.selectedSegmentIds.filter((id) => !ids.includes(id))
          : Array.from(new Set([...s.selectedSegmentIds, ...ids]))
      }
    }),
    setInspectorMode: (mode) => set({ inspectorMode: mode }),
    setFocusedTimelineItemId: (id) => set({ focusedTimelineItemId: id }),

    updateSegmentPosition: (id, start_time, end_time, lane_index, tts_duration_secs, tts_audio_path) =>
      set((s) => ({
        segmentPositions: {
          ...s.segmentPositions,
          [id]: {
            ...s.segmentPositions[id],
            start_time,
            end_time,
            lane_index,
            ...(tts_duration_secs !== undefined ? { tts_duration_secs } : {}),
            ...(tts_audio_path !== undefined ? { tts_audio_path } : {}),
          },
        },
      })),
    updateSegmentText: (id, text) =>
      set((s) => {
        const existing = s.segmentPositions[id] || {}
        return {
          segmentPositions: {
            ...s.segmentPositions,
            [id]: {
              ...existing,
              khmer_text: text,
              tts_audio_path: "", // Revert status to Pending on text change
            },
          },
        }
      }),
    clearSegmentPositions: () => set({ segmentPositions: {} }),

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
