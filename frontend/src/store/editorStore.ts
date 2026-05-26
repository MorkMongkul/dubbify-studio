// src/store/editorStore.ts
// Zustand store — UI-only editor state (no API data here)

import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'

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

  // Optimistic positions
  segmentPositions: Record<string, {
    start_time: number
    end_time: number
    speaker_id: string | null
    tts_duration_secs?: number
    tts_audio_path?: string
  }>
  updateSegmentPosition: (
    id: string,
    start_time: number,
    end_time: number,
    speaker_id: string | null,
    tts_duration_secs?: number,
    tts_audio_path?: string
  ) => void
  clearSegmentPositions: () => void

  // Mute & Solo States
  mutedTrackIds: Record<string, boolean>
  soloedTrackIds: Record<string, boolean>
  simulatingSegmentIds: Record<string, boolean>

  toggleMuteTrack: (speakerId: string | null) => void
  toggleSoloTrack: (speakerId: string | null) => void
  setSegmentSimulating: (segmentId: string, isSimulating: boolean) => void

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
  timelineHeight: 240,
  speakerPanelWidth: 200,
  activeSegmentId: null,
  editingSegmentId: null,
  selectedSpeakerId: null,
  leftPanelCollapsed: false,
  rightPanelCollapsed: false,
  segmentPositions: {},
  mutedTrackIds: {},
  soloedTrackIds: {},
  simulatingSegmentIds: {},
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

    updateSegmentPosition: (id, start_time, end_time, speaker_id, tts_duration_secs, tts_audio_path) =>
      set((s) => ({
        segmentPositions: {
          ...s.segmentPositions,
          [id]: {
            ...s.segmentPositions[id],
            start_time,
            end_time,
            speaker_id,
            ...(tts_duration_secs !== undefined ? { tts_duration_secs } : {}),
            ...(tts_audio_path !== undefined ? { tts_audio_path } : {}),
          },
        },
      })),
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
    setSegmentSimulating: (segmentId, isSimulating) =>
      set((s) => ({
        simulatingSegmentIds: {
          ...s.simulatingSegmentIds,
          [segmentId]: isSimulating,
        },
      })),

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
