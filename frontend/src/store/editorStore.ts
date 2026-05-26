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
  timelineHeight: 160,
  activeSegmentId: null,
  editingSegmentId: null,
  selectedSpeakerId: null,
  leftPanelCollapsed: false,
  rightPanelCollapsed: false,
}

export const useEditorStore = create<EditorStore>()(
  subscribeWithSelector((set, get) => ({
    ...DEFAULT_STATE,

    // ── Playback ──────────────────────────────────────────────
    setCurrentTime: (t) => set({ currentTime: t }),
    setDuration:    (d) => set({ duration: d }),
    setPlaying:     (p) => set({ isPlaying: p }),
    togglePlaying:  () => set((s) => ({ isPlaying: !s.isPlaying })),
    setVolume:      (v) => set({ volume: Math.max(0, Math.min(1, v)) }),
    setPlaybackRate:(r) => set({ playbackRate: r }),

    // ── Timeline ──────────────────────────────────────────────
    setZoom: (z) => set({ zoom: Math.max(0.25, Math.min(8, z)) }),
    zoomIn:  () => set((s) => ({ zoom: Math.min(8, s.zoom * 1.25) })),
    zoomOut: () => set((s) => ({ zoom: Math.max(0.25, s.zoom * 0.8) })),
    resetZoom: () => set({ zoom: 1 }),
    setTimelineScrollLeft: (x) => set({ timelineScrollLeft: x }),
    setTimelineHeight: (h) => set({ timelineHeight: Math.max(120, Math.min(400, h)) }),

    // ── Selection ─────────────────────────────────────────────
    setActiveSegment: (id) => {
      const current = get().activeSegmentId
      if (current !== id) set({ activeSegmentId: id })
    },
    setEditingSegment: (id) => set({ editingSegmentId: id }),
    setSelectedSpeaker: (id) => set({ selectedSpeakerId: id }),

    // ── Panels ────────────────────────────────────────────────
    toggleLeftPanel:  () => set((s) => ({ leftPanelCollapsed: !s.leftPanelCollapsed })),
    toggleRightPanel: () => set((s) => ({ rightPanelCollapsed: !s.rightPanelCollapsed })),
    setLeftPanelCollapsed:  (v) => set({ leftPanelCollapsed: v }),
    setRightPanelCollapsed: (v) => set({ rightPanelCollapsed: v }),

    // ── Reset ─────────────────────────────────────────────────
    resetEditor: () => set(DEFAULT_STATE),
  }))
)

// Selector helpers for performance
export const useCurrentTime      = () => useEditorStore((s) => s.currentTime)
export const useIsPlaying        = () => useEditorStore((s) => s.isPlaying)
export const useDuration         = () => useEditorStore((s) => s.duration)
export const useZoom             = () => useEditorStore((s) => s.zoom)
export const useActiveSegmentId  = () => useEditorStore((s) => s.activeSegmentId)
export const useEditingSegmentId = () => useEditorStore((s) => s.editingSegmentId)
