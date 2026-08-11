// src/components/timeline/TimelineEditor.tsx
import { memo, useRef, useCallback, useEffect, useState, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import WaveSurfer from 'wavesurfer.js'
import {
  ZoomIn, ZoomOut, Maximize2, AlertTriangle, Loader2, Scissors, X, RefreshCw,
  MousePointer2, SplitSquareHorizontal, Copy, Trash2, Magnet, Wand2, Layers,
} from 'lucide-react'
import { toast } from 'sonner'
import type { Segment, Speaker, Job, SegmentUpdate } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { useUpdateSegment, useDeleteSegment, useSynthesizeSegment, useAutofitSegments, useTidyLanes } from '@/hooks/useApi'
import { segments as segmentsApi } from '@/api/client'
import { PipelineStepper } from '@/features/upload/PipelineStepper'
import { timeToPixels, pixelsToTime, formatTime, getSpeakerColor, hexToRgba, cn, isJobRunning, getJobStatusConfig } from '@/lib/utils'
import { recordSegmentChange, recordSegmentsChange, recordSegmentDelete, recordSegmentsDelete, recordSegmentCreate, recordSegmentSplit } from '@/lib/historyHelpers'
import { Tooltip } from '@/components/ui/Tooltip'

const PX_PER_SEC = 100 // base pixels per second at zoom=1
const LANE_HEIGHT = 64  // clip row height — also the divisor for drop-target lane math
const STEM_HEIGHT = 64  // BGM / Vocals stem rows
const MIN_CLIP_SECS = 0.1

// Format playhead time down to fractions of a second: e.g., 0:01.23
function formatPlayheadTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '0:00.00'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const cs = Math.floor((seconds % 1) * 100)
  return `${m}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`
}

interface TimelineEditorProps {
  segments: Segment[]
  speakers: Speaker[]
  duration: number
  className?: string
  jobId?: string
  projectId?: string
  job?: Job
  onAnalyze?: (maxSpeakers?: number) => void
  analyzing?: boolean
}

export function TimelineEditor({ segments, speakers, duration, className, jobId, projectId, job, onAnalyze, analyzing }: TimelineEditorProps) {
  // Optional cap on detected speakers (curbs over-clustering). Blank = auto.
  const [maxSpeakers, setMaxSpeakers] = useState<string>('')
  const isStage1Processing = job?.status === 'extracting' || job?.status === 'separating' || job?.status === 'pending'
  const isStemsReady       = job?.status === 'stems_ready'
  // Once analysis has been triggered the original vocals are replaced by TTS — auto-mute them
  const vocalsReplaced = !!job && !isStemsReady && !isStage1Processing

  const containerRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const rulerRef  = useRef<HTMLDivElement>(null)
  // Top of the lane rows specifically (excludes BGM/Vocals/Speakers-legend
  // rows above them, whose combined height varies) — used to translate a
  // drop's Y position into the correct lane index.
  const lanesRef = useRef<HTMLDivElement>(null)
  // Ref (not state): only the playhead subscription reads it, and toggling it
  // must not re-render the timeline at scrub start/end.
  const isDraggingRef = useRef(false)
  // The big scrollable content div (playhead/grid/lanes live inside it) — the
  // marquee rectangle is positioned in its coordinate space.
  const contentRef = useRef<HTMLDivElement>(null)
  // Marquee visuals are driven imperatively (direct style writes), matching
  // how the playhead and clip drags avoid re-rendering per pointer move.
  const marqueeRef = useRef<HTMLDivElement>(null)
  const [showZoomTooltip, setShowZoomTooltip] = useState(false)
  const [isDraggingSlider, setIsDraggingSlider] = useState(false)

  // Per-field selectors — deliberately NOT subscribing to currentTime here:
  // the playhead is driven imperatively (refs + store subscription below), so
  // playback/scrubbing never re-renders the timeline tree.
  const zoom              = useEditorStore((s) => s.zoom)
  const activeSegmentId   = useEditorStore((s) => s.activeSegmentId)
  const volume            = useEditorStore((s) => s.volume)
  const mutedTrackIds     = useEditorStore((s) => s.mutedTrackIds)
  const soloedTrackIds    = useEditorStore((s) => s.soloedTrackIds)
  const timelineHeight    = useEditorStore((s) => s.timelineHeight)
  const speakerPanelWidth = useEditorStore((s) => s.speakerPanelWidth)
  const setCurrentTime    = useEditorStore((s) => s.setCurrentTime)
  const setActiveSegment  = useEditorStore((s) => s.setActiveSegment)
  const zoomIn            = useEditorStore((s) => s.zoomIn)
  const zoomOut           = useEditorStore((s) => s.zoomOut)
  const updateSegmentPosition = useEditorStore((s) => s.updateSegmentPosition)
  const toggleMuteTrack   = useEditorStore((s) => s.toggleMuteTrack)
  const setSpeakerPanelWidth = useEditorStore((s) => s.setSpeakerPanelWidth)
  const setZoom           = useEditorStore((s) => s.setZoom)
  const setInspectorMode  = useEditorStore((s) => s.setInspectorMode)
  const setFocusedTimelineItemId = useEditorStore((s) => s.setFocusedTimelineItemId)
  const activeTool        = useEditorStore((s) => s.activeTool)
  const setActiveTool     = useEditorStore((s) => s.setActiveTool)
  const snapEnabled       = useEditorStore((s) => s.snapEnabled)
  const toggleSnap        = useEditorStore((s) => s.toggleSnap)
  const selectedSegmentIds = useEditorStore((s) => s.selectedSegmentIds)
  const setSelectedSegments = useEditorStore((s) => s.setSelectedSegments)
  const toggleSelectSegment = useEditorStore((s) => s.toggleSelectSegment)

  const { mutate: updateSegment, isPending: isUpdatingSegment, variables: updateVariables } = useUpdateSegment()
  const { mutate: deleteSegment } = useDeleteSegment()
  const { mutate: regenerateSegment, isPending: isRegenerating, variables: regenVariables } = useSynthesizeSegment()
  const autofitSegments = useAutofitSegments()
  const tidyLanes = useTidyLanes()
  const clearSegmentPositions = useEditorStore((s) => s.clearSegmentPositions)
  const qc = useQueryClient()

  const handleAutofit = () => {
    if (!jobId) return
    autofitSegments.mutate(jobId, {
      onSuccess: ({ fitted, skipped, missing }) => {
        clearSegmentPositions() // drop stale optimistic durations
        if (fitted === 0 && missing === 0) toast.info(`All ${skipped} clips already fit their slots`)
        else toast.success(
          `Fitted ${fitted} clip(s) to their slots` +
          (skipped ? `, ${skipped} already fit` : '') +
          (missing ? ` — ${missing} missing audio file(s) skipped` : '')
        )
      },
      onError: (err: unknown) => {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        toast.error(detail ?? 'Auto-fit failed')
      },
    })
  }

  const handleTidyLanes = () => {
    if (!jobId) return
    tidyLanes.mutate(jobId, {
      onSuccess: ({ changed, lanes }) => {
        clearSegmentPositions() // drop stale optimistic lane positions
        toast.success(changed === 0
          ? 'Tracks already tidy'
          : `Repacked ${changed} clip(s) into ${lanes} lane(s)`)
      },
      onError: () => toast.error('Tidy failed'),
    })
  }

  // Delete / Backspace removes the multi-selection when one exists (single
  // undo entry restores all of it), else the active segment.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'AUDIO' || (e.target as HTMLElement).isContentEditable) return
      if (!jobId) return

      const ids = useEditorStore.getState().selectedSegmentIds
      if (ids.length > 0) {
        e.preventDefault()
        const targets = segments.filter((s) => ids.includes(s.id))
        if (targets.length === 0) return
        Promise.all(targets.map((t) => segmentsApi.delete(t.id)))
          .then(() => qc.invalidateQueries({ queryKey: ['segments', jobId] }))
          .catch(() => {
            toast.error('Failed to delete some clips')
            qc.invalidateQueries({ queryKey: ['segments', jobId] })
          })
        recordSegmentsDelete(qc, jobId, targets)
        useEditorStore.setState({ selectedSegmentIds: [], activeSegmentId: null })
        toast.success(`Deleted ${targets.length} clips`)
        return
      }

      if (!activeSegmentId) return
      e.preventDefault()
      const seg = segments.find((s) => s.id === activeSegmentId)
      deleteSegment({ segmentId: activeSegmentId, jobId })
      if (seg) recordSegmentDelete(qc, jobId, seg)
      setActiveSegment(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeSegmentId, jobId, deleteSegment, setActiveSegment, segments, qc])

  const totalWidth = Math.max(timeToPixels(duration || 60, zoom, PX_PER_SEC), 800)

  // Stem waveform paths — use tiny 8kHz mono preview files so Web Audio decodes instantly.
  // Full stems (~19MB) are kept for playback; preview files (~1.7MB) are for visualization only.
  const noVocalsPreviewPath = jobId && projectId ? `uploads/${projectId}/${jobId}/no_vocals.preview.wav` : null
  const vocalsPreviewPath   = jobId && projectId ? `uploads/${projectId}/${jobId}/vocals.preview.wav`    : null

  // Timeline rows are free lanes, independent of speaker — a lane is just a
  // vertical position, and clips of any speaker can live on any lane. One
  // extra empty lane always trails the highest used one so there's somewhere
  // to drag a clip to create a brand new lane.
  const laneIndices = useMemo(() => {
    const usedLanes = new Set(segments.map((s) => s.lane_index ?? 0))
    const maxLane = usedLanes.size > 0 ? Math.max(...usedLanes) : 0
    return Array.from({ length: maxLane + 2 }, (_, i) => i)
  }, [segments])

  // Compute speaker color map
  const speakerColorMap = useMemo(() => {
    const map = new Map<string, string>()
    speakers.forEach((sp, i) => {
      map.set(sp.id, sp.color ?? getSpeakerColor(i))
    })
    return map
  }, [speakers])

  // Latest segments for stable callbacks (so React.memo on segments holds)
  const segmentsRef = useRef(segments)
  useEffect(() => { segmentsRef.current = segments }, [segments])

  // ── Stable per-segment callbacks — required for memo(InteractiveSegment)
  // to actually skip re-renders (inline closures would re-create per render).
  const handleSegmentUpdateBackend = useCallback((id: string, startTime: number, endTime: number, laneIndex: number) => {
    updateSegment({
      segmentId: id,
      data: { start_time: startTime, end_time: endTime, lane_index: laneIndex },
    })
  }, [updateSegment])

  const handleSegmentHistory = useCallback((id: string, before: SegmentUpdate, after: SegmentUpdate) => {
    if (!jobId) return
    recordSegmentChange(qc, jobId, id, before, after, 'Move clip')
  }, [jobId, qc])

  const handleSegmentsGroupHistory = useCallback((changes: { id: string; before: SegmentUpdate; after: SegmentUpdate }[]) => {
    if (!jobId) return
    recordSegmentsChange(qc, jobId, changes)
  }, [jobId, qc])

  const handleSegmentSelect = useCallback((seg: Segment) => {
    setCurrentTime(seg.start_time)
    setActiveSegment(seg.id)
    setInspectorMode('audio_clip_settings')
    setFocusedTimelineItemId(seg.id)
    // A plain click replaces any multi-selection with just this clip —
    // standard NLE behaviour; Shift+click (handled in the clip) adds instead.
    if (useEditorStore.getState().selectedSegmentIds.length) {
      useEditorStore.getState().setSelectedSegments([])
    }
  }, [setCurrentTime, setActiveSegment, setInspectorMode, setFocusedTimelineItemId])

  const handleSegmentDelete = useCallback((id: string) => {
    if (!jobId) return
    const target = segmentsRef.current.find((s) => s.id === id)
    deleteSegment({ segmentId: id, jobId })
    setActiveSegment(null)
    if (target) recordSegmentDelete(qc, jobId, target)
  }, [jobId, deleteSegment, qc, setActiveSegment])

  const handleSegmentRegenerate = useCallback((id: string) => {
    if (jobId) regenerateSegment({ segmentId: id, jobId })
  }, [jobId, regenerateSegment])

  // ── Split ───────────────────────────────────────────────────────────────
  // Cuts one clip into two at `atTime`: the original is shortened and a new
  // clip takes over the tail, inheriting speaker/lane/voice/text. The original
  // keeps its generated audio (the player's auto-fit absorbs the new length,
  // same as trimming); the tail starts with no audio so it reads as pending.
  const [splittingId, setSplittingId] = useState<string | null>(null)
  const handleSplit = useCallback(async (seg: Segment, atTime: number) => {
    if (!jobId || splittingId) return
    // Reached from both the blade tool and the Split button, so keep the
    // message about the cut point rather than naming one of them.
    if (atTime <= seg.start_time + MIN_CLIP_SECS || atTime >= seg.end_time - MIN_CLIP_SECS) {
      toast.error('Cut point is too close to the clip edge')
      return
    }
    const t = Math.round(atTime * 1000) / 1000
    const originalEnd = seg.end_time
    setSplittingId(seg.id)
    try {
      await segmentsApi.update(seg.id, { end_time: t })
      const created = await segmentsApi.create(jobId, {
        speaker_id: seg.speaker_id ?? null,
        voice_id: seg.voice_id ?? null,
        lane_index: seg.lane_index ?? 0,
        start_time: t,
        end_time: originalEnd,
        source_text: seg.source_text,
        english_text: seg.english_text,
        khmer_text: seg.khmer_text,
        notes: seg.notes,
      })
      // Keep the optimistic position store in step with the shortened original
      updateSegmentPosition(seg.id, seg.start_time, t, seg.lane_index ?? 0)
      await qc.invalidateQueries({ queryKey: ['segments', jobId] })
      recordSegmentSplit(qc, jobId, seg.id, originalEnd, t, created)
      setActiveSegment(created.id)
      toast.success('Clip split — edit each part, then generate voice')
    } catch {
      toast.error('Failed to split clip')
    } finally {
      setSplittingId(null)
    }
  }, [jobId, qc, splittingId, updateSegmentPosition, setActiveSegment])

  // Split whatever clip the playhead is currently over (active clip first)
  const handleSplitAtPlayhead = useCallback(() => {
    const t = useEditorStore.getState().currentTime
    const list = segmentsRef.current
    const target =
      list.find((s) => s.id === activeSegmentId && t > s.start_time && t < s.end_time)
      ?? list.find((s) => t > s.start_time && t < s.end_time)
    if (!target) {
      toast.error('Move the playhead over a clip to split it')
      return
    }
    handleSplit(target, t)
  }, [activeSegmentId, handleSplit])

  // ── Duplicate ───────────────────────────────────────────────────────────
  const handleDuplicate = useCallback(async () => {
    if (!jobId || !activeSegmentId) return
    const seg = segmentsRef.current.find((s) => s.id === activeSegmentId)
    if (!seg) return
    const lane = seg.lane_index ?? 0
    const length = seg.end_time - seg.start_time
    // Drop the copy after the original, pushed clear of anything in the way
    const neighbours = segmentsRef.current.filter((s) => s.id !== seg.id && (s.lane_index ?? 0) === lane)
    const start = resolveDragOverlap(seg.end_time, length, neighbours)
    try {
      const created = await segmentsApi.create(jobId, {
        speaker_id: seg.speaker_id ?? null,
        voice_id: seg.voice_id ?? null,
        lane_index: lane,
        start_time: Math.round(start * 1000) / 1000,
        end_time: Math.round((start + length) * 1000) / 1000,
        source_text: seg.source_text,
        english_text: seg.english_text,
        khmer_text: seg.khmer_text,
        notes: seg.notes,
      })
      await qc.invalidateQueries({ queryKey: ['segments', jobId] })
      recordSegmentCreate(qc, jobId, created, 'Duplicate clip')
      setActiveSegment(created.id)
      toast.success('Clip duplicated')
    } catch {
      toast.error('Failed to duplicate clip')
    }
  }, [jobId, activeSegmentId, qc, setActiveSegment])

  const handleDeleteActive = useCallback(() => {
    if (!activeSegmentId || !jobId) return
    handleSegmentDelete(activeSegmentId)
  }, [activeSegmentId, jobId, handleSegmentDelete])

  // ── Tool shortcuts (V select · C blade · S split · ⌘D duplicate) ────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'AUDIO' || (e.target as HTMLElement)?.isContentEditable) return
      const key = e.key.toLowerCase()
      if ((e.metaKey || e.ctrlKey) && key === 'd') { e.preventDefault(); handleDuplicate(); return }
      // ⌘B / Ctrl+B — the split shortcut CapCut users already have in muscle memory
      if ((e.metaKey || e.ctrlKey) && key === 'b') { e.preventDefault(); handleSplitAtPlayhead(); return }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (key === 'v') setActiveTool('select')
      else if (key === 'c') setActiveTool('blade')
      else if (key === 's') { e.preventDefault(); handleSplitAtPlayhead() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setActiveTool, handleSplitAtPlayhead, handleDuplicate])

  // ── Imperative playhead ────────────────────────────────────────────────
  // currentTime changes 4×/s during playback and 60+×/s while scrubbing —
  // moving the playhead via direct style writes (compositor-cheap) instead of
  // React state keeps those ticks from re-rendering every lane/segment/tick.
  const rulerPlayheadRef  = useRef<HTMLDivElement>(null)
  const trackPlayheadRef  = useRef<HTMLDivElement>(null)
  const playheadBadgeRef  = useRef<HTMLDivElement>(null)
  const zoomLiveRef       = useRef(zoom)

  const positionPlayhead = useCallback((t: number) => {
    const x = timeToPixels(t, zoomLiveRef.current, PX_PER_SEC)
    if (rulerPlayheadRef.current) rulerPlayheadRef.current.style.left = `${x}px`
    if (trackPlayheadRef.current) trackPlayheadRef.current.style.left = `calc(var(--speaker-width) + ${x}px)`
    if (playheadBadgeRef.current) playheadBadgeRef.current.textContent = formatPlayheadTime(t)

    // Auto-scroll playhead into view (skipped mid-scrub)
    if (!isDraggingRef.current) {
      const container = scrollRef.current
      if (container) {
        const { scrollLeft, clientWidth } = container
        const margin = clientWidth * 0.3
        if (x < scrollLeft + margin || x > scrollLeft + clientWidth - margin) {
          container.scrollLeft = x - clientWidth * 0.4
        }
      }
    }
  }, [])

  // Reposition on zoom changes (zoom DOES re-render — widths change anyway)
  useEffect(() => {
    zoomLiveRef.current = zoom
    positionPlayhead(useEditorStore.getState().currentTime)
  }, [zoom, positionPlayhead])

  // Follow currentTime without re-rendering
  useEffect(() => {
    const unsub = useEditorStore.subscribe(
      (s) => s.currentTime,
      (t) => positionPlayhead(t),
    )
    return unsub
  }, [positionPlayhead])

  // Sync ruler scroll with content scroll
  const onContentScroll = useCallback(() => {
    if (rulerRef.current && scrollRef.current) {
      rulerRef.current.scrollLeft = scrollRef.current.scrollLeft
    }
  }, [])

  // Click/drag on ruler or tracks to seek
  const seekFromEvent = useCallback((clientX: number, rect: DOMRect) => {
    const scrollLeft = scrollRef.current?.scrollLeft ?? 0
    const x = clientX - rect.left + scrollLeft
    const t = pixelsToTime(x, zoom, PX_PER_SEC)
    setCurrentTime(Math.max(0, Math.min(duration, t)))
  }, [zoom, duration, setCurrentTime])

  const onRulerMouseDown = useCallback((e: React.MouseEvent) => {
    isDraggingRef.current = true
    seekFromEvent(e.clientX, e.currentTarget.getBoundingClientRect())
    const onMove  = (ev: MouseEvent) => seekFromEvent(ev.clientX, (e.currentTarget as HTMLElement).getBoundingClientRect())
    const onUp    = () => { isDraggingRef.current = false; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [seekFromEvent])

  const seekFromTrackEvent = useCallback((clientX: number) => {
    const scrollEl = scrollRef.current
    if (!scrollEl) return
    const rect = scrollEl.getBoundingClientRect()
    const scrollLeft = scrollEl.scrollLeft
    const x = clientX - rect.left + scrollLeft - speakerPanelWidth
    const t = pixelsToTime(x, zoom, PX_PER_SEC)
    setCurrentTime(Math.max(0, Math.min(duration, t)))
  }, [zoom, duration, speakerPanelWidth, setCurrentTime])

  // Empty-track-space gesture: press seeks the playhead (immediate feedback);
  // with the select tool, dragging past a small threshold becomes a marquee —
  // a rubber-band rectangle that selects every clip it touches (Shift = add
  // to the existing selection). Continuous drag-scrubbing lives on the ruler.
  const onTracksMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    const scrollEl = scrollRef.current
    const contentEl = contentRef.current
    if (!scrollEl || !contentEl) return
    const scrollRect = scrollEl.getBoundingClientRect()
    if (e.clientX - scrollRect.left < speakerPanelWidth) return // track headers

    isDraggingRef.current = true
    seekFromTrackEvent(e.clientX)

    const additive = e.shiftKey
    const baseSelection = additive ? useEditorStore.getState().selectedSegmentIds : []
    const startClientX = e.clientX
    const startClientY = e.clientY
    // Content-space geometry, captured once per gesture (zoom can't change
    // mid-drag; a mid-marquee wheel-scroll is ignored, same as clip drags).
    const contentRect = contentEl.getBoundingClientRect()
    const lanesRect = lanesRef.current?.getBoundingClientRect()
    const lanesTop = lanesRect ? lanesRect.top - contentRect.top : 0
    const startX = startClientX - contentRect.left
    const startY = startClientY - contentRect.top
    const canMarquee = activeTool === 'select'

    // Clip hit-boxes in content space. laneIndices is a dense 0..maxLane+1
    // range, so a clip's row offset is simply lane_index * LANE_HEIGHT.
    const boxes = canMarquee ? segmentsRef.current.map((s) => ({
      id: s.id,
      x1: speakerPanelWidth + timeToPixels(s.start_time, zoom, PX_PER_SEC),
      x2: speakerPanelWidth + timeToPixels(s.end_time, zoom, PX_PER_SEC),
      y1: lanesTop + (s.lane_index ?? 0) * LANE_HEIGHT,
      y2: lanesTop + ((s.lane_index ?? 0) + 1) * LANE_HEIGHT,
    })) : []

    let marqueeActive = false
    let raf: number | null = null
    let lastKey: string | null = null

    const applyMarquee = (cx: number, cy: number) => {
      const x1 = Math.min(startX, cx), x2 = Math.max(startX, cx)
      const y1 = Math.min(startY, cy), y2 = Math.max(startY, cy)
      const el = marqueeRef.current
      if (el) {
        el.style.display = 'block'
        el.style.left = `${x1}px`
        el.style.top = `${y1}px`
        el.style.width = `${x2 - x1}px`
        el.style.height = `${y2 - y1}px`
      }
      const hit = boxes
        .filter((b) => b.x1 < x2 && b.x2 > x1 && b.y1 < y2 && b.y2 > y1)
        .map((b) => b.id)
      const merged = additive ? Array.from(new Set([...baseSelection, ...hit])) : hit
      // Store writes only when membership actually changes — the timeline
      // re-renders on selection updates, so don't spam it per pointer move.
      const key = merged.join('|')
      if (key !== lastKey) {
        lastKey = key
        setSelectedSegments(merged)
      }
    }

    const onMove = (ev: MouseEvent) => {
      if (!marqueeActive) {
        if (!canMarquee) return
        if (Math.abs(ev.clientX - startClientX) < 4 && Math.abs(ev.clientY - startClientY) < 4) return
        marqueeActive = true
      }
      const cx = ev.clientX - contentRect.left
      const cy = ev.clientY - contentRect.top
      if (raf == null) {
        raf = requestAnimationFrame(() => { raf = null; applyMarquee(cx, cy) })
      }
    }
    const onUp = (ev: MouseEvent) => {
      isDraggingRef.current = false
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      if (raf != null) cancelAnimationFrame(raf)
      if (marqueeActive) {
        applyMarquee(ev.clientX - contentRect.left, ev.clientY - contentRect.top)
        if (marqueeRef.current) marqueeRef.current.style.display = 'none'
      } else if (!additive && useEditorStore.getState().selectedSegmentIds.length) {
        // Clean click on empty space clears the multi-selection
        setSelectedSegments([])
      }
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [seekFromTrackEvent, speakerPanelWidth, zoom, activeTool, setSelectedSegments])

  const handleHorizontalResizeDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const container = containerRef.current
    if (!container) return

    container.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const startWidth = parseFloat(getComputedStyle(container).getPropertyValue('--speaker-width')) || speakerPanelWidth

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const deltaX = moveEvent.clientX - startX
      const newWidth = Math.max(160, Math.min(300, startWidth + deltaX))
      container.style.setProperty('--speaker-width', `${newWidth}px`)
    }

    const handlePointerUp = (upEvent: PointerEvent) => {
      container.releasePointerCapture(upEvent.pointerId)
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)

      const finalWidth = parseFloat(getComputedStyle(container).getPropertyValue('--speaker-width')) || speakerPanelWidth
      setSpeakerPanelWidth(finalWidth)
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
  }, [speakerPanelWidth, setSpeakerPanelWidth])

  // Throttle zoom-slider commits to one per animation frame — the slider's
  // native `input` event can fire far faster than 60fps, and every commit
  // triggers a re-render of every track/segment, so batching keeps it smooth.
  const zoomRafRef = useRef<number | null>(null)
  const pendingZoomRef = useRef<number | null>(null)
  const commitZoomThrottled = useCallback((z: number) => {
    pendingZoomRef.current = z
    if (zoomRafRef.current != null) return
    zoomRafRef.current = requestAnimationFrame(() => {
      if (pendingZoomRef.current != null) setZoom(pendingZoomRef.current)
      zoomRafRef.current = null
    })
  }, [setZoom])

  // Fit the whole job duration into the visible track viewport at once.
  const handleZoomToFit = useCallback(() => {
    const container = scrollRef.current
    if (!container || !duration) return
    const viewportWidth = container.clientWidth - speakerPanelWidth
    if (viewportWidth <= 0) return
    setZoom(viewportWidth / (duration * PX_PER_SEC))
    container.scrollLeft = 0
  }, [duration, speakerPanelWidth, setZoom])


  // Time ruler ticks — memoized: at high zoom this is one node per second of
  // video (thousands for a film), and it only depends on zoom + duration.
  const rulerTicks = useMemo(() => {
    const tickEvery = zoom >= 2 ? 1 : zoom >= 1 ? 5 : zoom >= 0.5 ? 10 : 30 // seconds
    const ticks = []
    for (let t = 0; t <= (duration || 60); t += tickEvery) {
      const x = timeToPixels(t, zoom, PX_PER_SEC)
      ticks.push(
        <div key={t} className="absolute flex flex-col items-center" style={{ left: x }}>
          <div className="h-2.5 w-px bg-white/25" />
          <span className="text-[10.5px] text-white/40 font-mono mt-0.5 whitespace-nowrap tabular-nums">
            {formatTime(t)}
          </span>
        </div>
      )
    }
    return ticks
  }, [zoom, duration])

  // Initial paint only — afterwards the store subscription moves the playhead
  const initialPlayheadX = timeToPixels(useEditorStore.getState().currentTime, zoom, PX_PER_SEC)

  return (
    <div
      ref={containerRef}
      className={cn('flex flex-col relative select-none shrink-0', className)}
      style={{
        height: `${timelineHeight}px`,
        '--speaker-width': `${speakerPanelWidth}px`,
      } as React.CSSProperties}
    >
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3 px-3 border-b border-white/[0.06] shrink-0 bg-zinc-900 select-none w-full relative h-11">
        {/* Left: editing tools */}
        <div className="flex items-center gap-1.5 z-10">
          {/* Tool mode — segmented control */}
          <div className="flex items-center gap-0.5 rounded-lg bg-zinc-950/60 border border-white/[0.07] p-0.5">
            <Tooltip content="Select — drag clips · drag empty space to box-select (V)" side="bottom">
              <button
                onClick={() => setActiveTool('select')}
                aria-pressed={activeTool === 'select'}
                className={cn(
                  'h-7 w-8 rounded-md flex items-center justify-center transition-colors',
                  activeTool === 'select' ? 'bg-brand/25 text-brand-200' : 'text-white/40 hover:text-white hover:bg-white/5'
                )}
              >
                <MousePointer2 size={14} />
              </button>
            </Tooltip>
            <Tooltip content="Blade tool — click a clip to cut it (C)" side="bottom">
              <button
                onClick={() => setActiveTool('blade')}
                aria-pressed={activeTool === 'blade'}
                className={cn(
                  'h-7 w-8 rounded-md flex items-center justify-center transition-colors',
                  activeTool === 'blade' ? 'bg-brand/25 text-brand-200' : 'text-white/40 hover:text-white hover:bg-white/5'
                )}
              >
                <Scissors size={14} />
              </button>
            </Tooltip>
          </div>

          <div className="w-px h-5 bg-white/[0.08] mx-0.5" />

          {/* Clip actions — operate on the selected clip */}
          <Tooltip content="Split at playhead (S or ⌘B)" side="bottom">
            <button
              onClick={handleSplitAtPlayhead}
              className="h-7 px-2 rounded-md flex items-center gap-1.5 text-white/50 hover:text-white hover:bg-white/8 transition-colors text-[11.5px] font-medium"
            >
              <SplitSquareHorizontal size={14} /> Split
            </button>
          </Tooltip>
          <Tooltip content="Duplicate clip (⌘D)" side="bottom">
            <button
              onClick={handleDuplicate}
              disabled={!activeSegmentId}
              className="h-7 w-7 rounded-md flex items-center justify-center text-white/50 hover:text-white hover:bg-white/8 disabled:opacity-25 disabled:hover:bg-transparent transition-colors"
            >
              <Copy size={13} />
            </button>
          </Tooltip>
          <Tooltip content="Delete selection (Del)" side="bottom">
            <button
              onClick={handleDeleteActive}
              disabled={!activeSegmentId}
              className="h-7 w-7 rounded-md flex items-center justify-center text-white/50 hover:text-red-400 hover:bg-red-500/15 disabled:opacity-25 disabled:hover:bg-transparent disabled:hover:text-white/50 transition-colors"
            >
              <Trash2 size={13} />
            </button>
          </Tooltip>

          <div className="w-px h-5 bg-white/[0.08] mx-0.5" />

          <Tooltip content={snapEnabled ? 'Snapping on — edges stick to clips & playhead' : 'Snapping off'} side="bottom">
            <button
              onClick={toggleSnap}
              aria-pressed={snapEnabled}
              className={cn(
                'h-7 w-7 rounded-md flex items-center justify-center transition-colors',
                snapEnabled ? 'bg-brand/20 text-brand-200' : 'text-white/40 hover:text-white hover:bg-white/8'
              )}
            >
              <Magnet size={13} />
            </button>
          </Tooltip>

          <div className="w-px h-5 bg-white/[0.08] mx-0.5" />

          <Tooltip content="Auto-fit voices — speed every clip so its audio exactly fills its slot (long lines faster, short lines slower)" side="bottom">
            <button
              onClick={handleAutofit}
              disabled={!jobId || autofitSegments.isPending}
              className="h-7 px-2 rounded-md flex items-center gap-1.5 text-white/50 hover:text-white hover:bg-white/8 disabled:opacity-25 transition-colors text-[11.5px] font-medium"
            >
              {autofitSegments.isPending ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
              Auto-fit
            </button>
          </Tooltip>
          <Tooltip content="Tidy tracks — pack scattered clips back into the fewest lanes (only true overlaps stay on separate lanes)" side="bottom">
            <button
              onClick={handleTidyLanes}
              disabled={!jobId || tidyLanes.isPending}
              className="h-7 px-2 rounded-md flex items-center gap-1.5 text-white/50 hover:text-white hover:bg-white/8 disabled:opacity-25 transition-colors text-[11.5px] font-medium"
            >
              {tidyLanes.isPending ? <Loader2 size={13} className="animate-spin" /> : <Layers size={13} />}
              Tidy
            </button>
          </Tooltip>

          {/* Multi-selection count — marquee/Shift+click feed the same
              selection the transcript's batch toolbar acts on */}
          {selectedSegmentIds.length > 0 && (
            <>
              <div className="w-px h-5 bg-white/[0.08] mx-0.5" />
              <span className="text-[10.5px] font-semibold text-purple-300 tabular-nums select-none">
                {selectedSegmentIds.length} selected
              </span>
              <Tooltip content="Clear selection" side="bottom">
                <button
                  onClick={() => setSelectedSegments([])}
                  aria-label="Clear selection"
                  className="h-6 w-6 rounded-md flex items-center justify-center text-white/40 hover:text-white hover:bg-white/8 transition-colors"
                >
                  <X size={11} />
                </button>
              </Tooltip>
            </>
          )}
        </div>

        {/* Center: label (hidden on narrow toolbars so tools never collide) */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 hidden 2xl:flex items-center gap-1 pointer-events-none">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-zinc-600 select-none">Timeline</span>
        </div>

        {/* Right: Zoom Controls */}
        <div className="flex items-center justify-end gap-1.5 z-10">
          <Tooltip content="Zoom out" side="bottom">
            <button
              className="h-7 w-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-white/8 transition-colors"
              onClick={zoomOut}
            >
              <ZoomOut size={14} />
            </button>
          </Tooltip>

          <div className="relative flex items-center group/zoom">
            {/* Tooltip bubble */}
            {/* Below the slider (over the ruler), not above it — above the
                toolbar is outside the panel's overflow-hidden bounds, where
                the bubble was clipped into a broken-looking fragment. */}
            <div
              className={cn(
                "absolute top-full left-1/2 -translate-x-1/2 mt-2 px-2 py-0.5 rounded bg-zinc-900 border border-zinc-700 text-[10px] font-mono font-bold text-white shadow-md pointer-events-none transition-all duration-150 z-30 whitespace-nowrap",
                (showZoomTooltip || isDraggingSlider)
                  ? "opacity-100 scale-100 translate-y-0"
                  : "opacity-0 scale-90 -translate-y-1"
              )}
            >
              {Math.round(zoom * 100)}%
              {/* Tooltip arrow (pointing up at the slider) */}
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 -mb-[1px] w-0 h-0 border-l-[4px] border-r-[4px] border-b-[4px] border-l-transparent border-r-transparent border-b-zinc-700" />
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 -mb-[2px] w-0 h-0 border-l-[4px] border-r-[4px] border-b-[4px] border-l-transparent border-r-transparent border-b-zinc-900" />
            </div>

            <input
              type="range"
              min="0.25"
              max="8"
              step="0.05"
              value={zoom}
              onChange={(e) => commitZoomThrottled(parseFloat(e.target.value))}
              onMouseEnter={() => setShowZoomTooltip(true)}
              onMouseLeave={() => setShowZoomTooltip(false)}
              onPointerDown={() => {
                setIsDraggingSlider(true)
                setShowZoomTooltip(true)
              }}
              onPointerUp={() => {
                setIsDraggingSlider(false)
              }}
              onFocus={() => setShowZoomTooltip(true)}
              onBlur={() => {
                setShowZoomTooltip(false)
                setIsDraggingSlider(false)
              }}
              className="w-20 md:w-24 appearance-none cursor-pointer bg-zinc-800 h-[2px] rounded-lg focus:outline-none opacity-85 hover:opacity-100 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:shadow [&::-moz-range-thumb]:h-2.5 [&::-moz-range-thumb]:w-2.5 [&::-moz-range-thumb]:bg-white [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0"
            />
          </div>

          <Tooltip content="Zoom in" side="bottom">
            <button
              className="h-7 w-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-white/8 transition-colors"
              onClick={zoomIn}
            >
              <ZoomIn size={14} />
            </button>
          </Tooltip>

          <div className="w-px h-5 bg-white/[0.08] mx-0.5" />

          <Tooltip content="Zoom to fit" side="bottom">
            <button
              className="h-7 w-7 rounded-md flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-white/8 transition-colors"
              onClick={handleZoomToFit}
            >
              <Maximize2 size={13} />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Main Timeline Area (Ruler + Tracks + Splitters) */}
      <div className="flex-1 min-h-0 flex flex-col relative">
        {/* Ruler Row */}
        <div className="flex h-8 bg-zinc-950/30 border-b border-white/[0.06] shrink-0 select-none relative">
          {/* Ruler Speaker Header Spacer */}
          <div
            className="h-full border-r border-white/[0.04] shrink-0 bg-zinc-900/95 z-20"
            style={{ width: 'var(--speaker-width)' }}
          />
          {/* Ruler Scroll Viewport */}
          <div
            ref={rulerRef}
            className="flex-1 overflow-hidden relative h-full"
            style={{ overflowX: 'hidden' }}
          >
            <div
              className="relative h-full cursor-crosshair select-none"
              style={{ width: totalWidth }}
              onMouseDown={onRulerMouseDown}
            >
              {rulerTicks}
              {/* Playhead on ruler — positioned imperatively via ref */}
              <div
                ref={rulerPlayheadRef}
                className="absolute top-0 bottom-0 w-px bg-status-error pointer-events-none z-30"
                style={{ left: initialPlayheadX, boxShadow: '0 0 6px rgba(239,68,68,0.8)' }}
              >
                <div className="w-0 h-0 border-l-[5px] border-r-[5px] border-t-[6px] border-l-transparent border-r-transparent border-t-status-error absolute -top-0 left-1/2 -translate-x-1/2" />
                {/* Floating Playhead Timestamp Badge — text set imperatively via ref */}
                <div
                  ref={playheadBadgeRef}
                  className="absolute top-[7px] left-1/2 -translate-x-1/2 bg-zinc-900 border border-red-500/50 px-2 py-0.5 rounded font-mono text-[11px] text-red-400 font-semibold shadow-md whitespace-nowrap select-none z-40 tabular-nums"
                >
                  {formatPlayheadTime(useEditorStore.getState().currentTime)}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Tracks */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-auto relative"
          onScroll={onContentScroll}
          style={{ minHeight: 0 }}
        >
          <div
            ref={contentRef}
            className="relative"
            style={{ width: `calc(${totalWidth}px + var(--speaker-width))`, minHeight: '100%' }}
            onMouseDown={onTracksMouseDown}
          >
            {/* Marquee (box-select) rectangle — shown/positioned imperatively */}
            <div
              ref={marqueeRef}
              className="absolute z-40 border border-purple-400/80 bg-purple-500/10 rounded-sm pointer-events-none"
              style={{ display: 'none' }}
            />

            {/* Playhead line through all tracks — positioned imperatively via ref */}
            <div
              ref={trackPlayheadRef}
              className="playhead-line"
              style={{ left: `calc(var(--speaker-width) + ${initialPlayheadX}px)` }}
            />

            {/* Grid lines */}
            <div
              className="absolute top-0 bottom-0 right-0 pointer-events-none"
              style={{
                left: 'var(--speaker-width)',
                backgroundImage: `repeating-linear-gradient(90deg, rgba(255,255,255,0.03) 0px, rgba(255,255,255,0.03) 1px, transparent 1px, transparent ${PX_PER_SEC * zoom}px)`,
              }}
            />

            {/* Background Music (BGM) Track */}
            <div
              className="relative border-b border-timeline-grid bg-zinc-900/30"
              style={{ height: STEM_HEIGHT }}
            >
              {/* Track label (sticky left) */}
              <div
                className="sticky left-0 z-10 h-full flex items-center px-2.5 gap-2 shrink-0 bg-zinc-900/95 backdrop-blur-sm border-r border-white/[0.06]"
                style={{ float: 'left', width: 'var(--speaker-width)' }}
              >
                <div
                  className="h-2.5 w-2.5 rounded-full shrink-0 bg-sky-500"
                />
                <span className="text-[13px] text-white/70 truncate grow min-w-0 pr-1 font-semibold">
                  BGM &amp; SFX
                </span>

                {/* Mute Track Control */}
                <div className="flex items-center gap-0.5 ml-auto shrink-0">
                  <button
                    onClick={() => toggleMuteTrack('__bgm__')}
                    className={cn(
                      "h-6 w-6 rounded-md text-[11px] font-bold flex items-center justify-center border transition-all select-none",
                      mutedTrackIds['__bgm__']
                        ? "bg-amber-500/20 text-amber-500 border-amber-500/30 hover:bg-amber-500/30"
                        : "bg-transparent text-white/35 border-transparent hover:bg-white/8 hover:text-white/70"
                    )}
                    title="Mute Background Music"
                  >
                    M
                  </button>
                </div>
              </div>

              {/* BGM audio block */}
              <div
                className="absolute top-1 bottom-1 rounded overflow-hidden"
                style={{
                  left: 'calc(var(--speaker-width) + 4px)',
                  width: `${timeToPixels(duration || 60, zoom, PX_PER_SEC)}px`,
                }}
              >
                {isStage1Processing ? (
                  <div className="absolute inset-0 rounded bg-sky-500/5 border border-sky-500/15">
                    <div className="absolute inset-0 bg-gradient-to-r from-transparent via-sky-500/10 to-transparent animate-pulse" />
                    <span className="absolute top-1 left-3 text-[10px] text-sky-400/40 font-medium select-none">Separating…</span>
                  </div>
                ) : (
                  <div className="absolute inset-0 rounded bg-sky-500/10 border border-sky-500/25">
                    {noVocalsPreviewPath && (
                      <StemWaveform
                        audioUrl={noVocalsPreviewPath}
                        color="#38bdf8"
                        pxPerSec={PX_PER_SEC * zoom}
                      />
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Vocals (Isolated) Track */}
            <div
              className="relative border-b border-timeline-grid bg-zinc-900/30"
              style={{ height: STEM_HEIGHT }}
            >
              {/* Track label — Analyze button lives here (sticky, always visible) */}
              <div
                className="sticky left-0 z-10 h-full flex items-center px-2.5 gap-2 shrink-0 bg-zinc-900/95 backdrop-blur-sm border-r border-white/[0.06]"
                style={{ float: 'left', width: 'var(--speaker-width)' }}
              >
                <div className={cn(
                  "h-2.5 w-2.5 rounded-full shrink-0",
                  isStage1Processing ? "bg-emerald-500/30 animate-pulse"
                    : vocalsReplaced   ? "bg-zinc-600"
                    : "bg-emerald-500"
                )} />
                <div className="flex flex-col min-w-0 grow pr-1">
                  <span className="text-[13px] text-white/70 truncate font-semibold leading-tight">
                    {vocalsReplaced ? 'Orig. Vocals' : 'Vocals'}
                  </span>
                  {vocalsReplaced && (
                    <span className="text-[8px] text-zinc-600 truncate leading-tight"></span>
                  )}
                </div>
                <div className="flex items-center gap-1 ml-auto shrink-0">
                  {/* M button — always visible when stems exist so user can toggle at any time */}
                  {!isStage1Processing && (
                    <button
                      onClick={() => toggleMuteTrack('__vocals__')}
                      className={cn(
                        "h-6 w-6 rounded-md text-[11px] font-bold flex items-center justify-center border transition-all select-none",
                        mutedTrackIds['__vocals__']
                          ? "bg-amber-500/20 text-amber-500 border-amber-500/30 hover:bg-amber-500/30"
                          : "bg-transparent text-white/35 border-transparent hover:bg-white/8 hover:text-white/70"
                      )}
                      title={mutedTrackIds['__vocals__'] ? "Unmute Vocals" : "Mute Vocals"}
                    >
                      M
                    </button>
                  )}
                  {/* Analyze button + optional max-speakers cap — shown at stems_ready */}
                  {isStemsReady && onAnalyze && (
                    <div className="flex items-center gap-1">
                      <input
                        type="number"
                        min={1}
                        max={20}
                        value={maxSpeakers}
                        onChange={(e) => setMaxSpeakers(e.target.value)}
                        placeholder="spk"
                        title="Max speakers (optional) — caps over-detection. Leave blank for auto."
                        disabled={analyzing}
                        className="w-10 px-1 py-0.5 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-200 text-[9px] focus:outline-none focus:border-emerald-500 disabled:opacity-60"
                      />
                      <button
                        onClick={() => {
                          const n = parseInt(maxSpeakers, 10)
                          onAnalyze(Number.isFinite(n) && n > 0 ? n : undefined)
                        }}
                        disabled={analyzing}
                        className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500 hover:bg-emerald-400 disabled:opacity-60 text-white text-[9px] font-bold shadow transition-all select-none"
                        title="Detect speakers and transcribe"
                      >
                        {analyzing
                          ? <><Loader2 size={9} className="animate-spin" />…</>
                          : <><Scissors size={9} />Analyze</>
                        }
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* Vocals audio block */}
              <div
                className="absolute top-1 bottom-1 rounded overflow-hidden"
                style={{
                  left: 'calc(var(--speaker-width) + 4px)',
                  width: `${timeToPixels(duration || 60, zoom, PX_PER_SEC)}px`,
                }}
              >
                {isStage1Processing ? (
                  <div className="absolute inset-0 rounded bg-emerald-500/5 border border-emerald-500/15">
                    <div className="absolute inset-0 bg-gradient-to-r from-transparent via-emerald-500/10 to-transparent animate-pulse" />
                    <span className="absolute top-1 left-3 text-[10px] text-emerald-400/40 font-medium select-none">Separating vocals…</span>
                  </div>
                ) : (
                  <div className={cn(
                    "absolute inset-0 rounded border transition-all",
                    vocalsReplaced
                      ? "bg-zinc-800/40 border-zinc-700/30 opacity-40"
                      : "bg-emerald-500/10 border-emerald-500/25"
                  )}>
                    {vocalsPreviewPath && (
                      <StemWaveform
                        audioUrl={vocalsPreviewPath}
                        color={vocalsReplaced ? "#71717a" : "#34d399"}
                        pxPerSec={PX_PER_SEC * zoom}
                      />
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Timeline lanes — free rows, independent of speaker */}
            <div ref={lanesRef}>
            {laneIndices.map((laneIdx) => {
              const laneSegments = segments.filter((s) => (s.lane_index ?? 0) === laneIdx)

              return (
                <div
                  key={laneIdx}
                  className="relative border-b border-timeline-grid"
                  style={{ height: LANE_HEIGHT }}
                >
                  {/* Track label (sticky left) */}
                  <div
                    className="sticky left-0 z-10 h-full flex items-center px-2.5 gap-2 shrink-0 bg-zinc-900/95 backdrop-blur-sm border-r border-white/[0.06]"
                    style={{ float: 'left', width: 'var(--speaker-width)' }}
                  >
                    <span className="text-[12.5px] font-medium text-white/45 truncate grow min-w-0 pr-1 select-none">
                      Track {laneIdx + 1}
                    </span>
                    <span className="text-[10.5px] text-white/25 tabular-nums shrink-0">
                      {laneSegments.length || ''}
                    </span>
                  </div>

                  {/* Segments */}
                  <div
                    className="absolute top-0 bottom-0 right-0"
                    style={{ left: 'var(--speaker-width)' }}
                  >
                    {laneSegments.map((seg) => {
                      const isActive = seg.id === activeSegmentId
                      const isApproved = seg.is_approved
                      const color = speakerColorMap.get(seg.speaker_id ?? '__none__') ?? getSpeakerColor(0)

                      return (
                        <InteractiveSegment
                          key={seg.id}
                          seg={seg}
                          color={color}
                          isActive={isActive}
                          isSelected={selectedSegmentIds.includes(seg.id)}
                          onToggleSelect={toggleSelectSegment}
                          isApproved={isApproved}
                          zoom={zoom}
                          duration={duration}
                          laneIndices={laneIndices}
                          allSegments={segments}
                          PX_PER_SEC={PX_PER_SEC}
                          scrollRef={scrollRef}
                          lanesRef={lanesRef}
                          updateSegmentPosition={updateSegmentPosition}
                          onUpdateBackend={handleSegmentUpdateBackend}
                          onRecordHistory={handleSegmentHistory}
                          onRecordGroupHistory={handleSegmentsGroupHistory}
                          onSelect={handleSegmentSelect}
                          onDelete={handleSegmentDelete}
                          onRegenerate={jobId ? handleSegmentRegenerate : undefined}
                          isPersisting={isUpdatingSegment && updateVariables?.segmentId === seg.id}
                          isRegenerating={isRegenerating && regenVariables?.segmentId === seg.id}
                          isSplitting={splittingId === seg.id}
                          mutedTrackIds={mutedTrackIds}
                          soloedTrackIds={soloedTrackIds}
                          volume={volume}
                          activeTool={activeTool}
                          snapEnabled={snapEnabled}
                          onSplit={handleSplit}
                        />
                      )
                    })}
                  </div>
                </div>
              )
            })}
            </div>

            {/* Empty state */}
            {segments.length === 0 && (
              <div className="flex flex-col items-center justify-center h-24 text-text-disabled text-xs gap-3 w-full">
                {job && isJobRunning(job.status) ? (
                  <>
                    <div className="flex items-center gap-2 text-brand-300 font-medium">
                      <Loader2 size={14} className="animate-spin" />
                      <span>{getJobStatusConfig(job.status).description}</span>
                    </div>
                    <div className="w-64">
                      <PipelineStepper job={job} compact />
                    </div>
                  </>
                ) : (
                  <span>No segments yet — process a video to populate the timeline</span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Vertical Splitter Handle for Speaker Panel Width */}
        <div
          className="absolute top-0 bottom-0 w-[4px] -translate-x-1/2 cursor-col-resize z-30 hover:bg-brand/50 active:bg-brand transition-all"
          style={{ left: 'var(--speaker-width)' }}
          onPointerDown={handleHorizontalResizeDown}
        />
      </div>
    </div>
  )
}

// ── Overlap resolution — segments on the same track must never overlap ──

// Dragging: duration is fixed, only position moves. Push the desired start
// to whichever side of a colliding neighbor is closer to where it was dropped.
function resolveDragOverlap(
  desiredStart: number,
  duration: number,
  neighbors: { start_time: number; end_time: number }[]
): number {
  let start = desiredStart
  const sorted = [...neighbors].sort((a, b) => a.start_time - b.start_time)
  for (const n of sorted) {
    const end = start + duration
    if (end <= n.start_time || start >= n.end_time) continue // no overlap with this one
    const pushLeftTo = n.start_time - duration
    const pushRightTo = n.end_time
    start = Math.abs(pushLeftTo - desiredStart) <= Math.abs(pushRightTo - desiredStart)
      ? Math.max(0, pushLeftTo)
      : pushRightTo
  }
  return Math.max(0, start)
}

// Trimming the left edge can't cross into whatever ends right before it.
function clampResizeLeft(newStartTime: number, endTime: number, neighbors: { end_time: number }[]): number {
  const floor = neighbors
    .filter((n) => n.end_time <= endTime)
    .reduce((max, n) => Math.max(max, n.end_time), 0)
  return Math.max(newStartTime, floor)
}

// Trimming the right edge can't cross into whatever starts right after it.
function clampResizeRight(newEndTime: number, startTime: number, neighbors: { start_time: number }[]): number {
  const ceilings = neighbors.filter((n) => n.start_time >= startTime).map((n) => n.start_time)
  const ceiling = ceilings.length ? Math.min(...ceilings) : Infinity
  return Math.min(newEndTime, ceiling)
}

// ── Interactive Segment Block with Pointer Gestures ─────────────────
interface InteractiveSegmentProps {
  seg: Segment
  color: string
  isActive: boolean
  /** Part of the multi-selection (marquee / Shift+click). */
  isSelected: boolean
  onToggleSelect: (id: string) => void
  isApproved: boolean
  zoom: number
  duration: number
  laneIndices: number[]
  allSegments: Segment[]
  PX_PER_SEC: number
  scrollRef: React.RefObject<HTMLDivElement | null>
  lanesRef: React.RefObject<HTMLDivElement | null>
  updateSegmentPosition: (
    id: string,
    start_time: number,
    end_time: number,
    lane_index: number,
    tts_duration_secs?: number,
    tts_audio_path?: string
  ) => void
  onUpdateBackend: (id: string, startTime: number, endTime: number, laneIndex: number) => void
  onRecordHistory: (id: string, before: SegmentUpdate, after: SegmentUpdate) => void
  /** One undo entry for a whole group drag (see recordSegmentsChange). */
  onRecordGroupHistory: (changes: { id: string; before: SegmentUpdate; after: SegmentUpdate }[]) => void
  onSelect: (seg: Segment) => void
  onDelete: (id: string) => void
  onRegenerate?: (id: string) => void
  isPersisting?: boolean
  isRegenerating?: boolean
  isSplitting?: boolean
  mutedTrackIds: Record<string, boolean>
  soloedTrackIds: Record<string, boolean>
  volume: number
  activeTool: 'select' | 'blade'
  snapEnabled: boolean
  onSplit: (seg: Segment, atTime: number) => void
}

// memo + the stable callbacks passed from TimelineEditor: segments only
// re-render when their own data/selection/zoom changes, not on every parent
// render — with hundreds of clips each carrying an SVG waveform, this is the
// difference between smooth and stuttering timeline interaction.
const InteractiveSegment = memo(function InteractiveSegment({
  seg, color, isActive, isSelected, onToggleSelect, isApproved, zoom, duration, laneIndices, allSegments, PX_PER_SEC, scrollRef, lanesRef,
  updateSegmentPosition, onUpdateBackend, onRecordHistory, onRecordGroupHistory, onSelect, onDelete, onRegenerate,
  isPersisting, isRegenerating, isSplitting,
  mutedTrackIds, soloedTrackIds, volume,
  activeTool, snapEnabled, onSplit,
}: InteractiveSegmentProps) {
  const elementRef = useRef<HTMLDivElement>(null)

  // Lazy waveform: only fetch+decode the TTS clip once this segment has
  // actually scrolled into view (then keep it — no churn on scroll-out).
  // Opening a large project used to fire one fetch+decode per clip at once.
  const [waveVisible, setWaveVisible] = useState(false)
  useEffect(() => {
    const el = elementRef.current
    if (!el || waveVisible) return
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setWaveVisible(true)
    }, { rootMargin: '200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [waveVisible])

  const left = timeToPixels(seg.start_time, zoom, PX_PER_SEC)
  const width = Math.max(4, timeToPixels(seg.end_time - seg.start_time, zoom, PX_PER_SEC))

  // "Line won't fit its slot" check.
  // Prefer the REAL synthesised duration; only estimate from text length
  // before any audio exists. The old estimate (0.12s per character) assumed
  // Latin-ish text — Khmer spends several code points on one spoken syllable,
  // so it over-estimated wildly and flagged essentially every clip, which made
  // the whole timeline red and buried the speaker colours.
  const SECS_PER_CHAR = 0.045
  const clipSecs = seg.end_time - seg.start_time
  const spokenSecs = seg.tts_duration_secs > 0
    ? seg.tts_duration_secs
    : (seg.khmer_text?.length ?? 0) * SECS_PER_CHAR
  // Playback auto-fits by speeding the clip up, so a small overflow is fine —
  // warn only when the required speed-up starts to sound unnatural.
  const isTooFast = spokenSecs > clipSecs * 1.25

  // Cache-busted URL — the backend overwrites the same file path on every
  // re-synthesis, so a version query param (tts_duration_secs changes on
  // every real synth) is what actually busts the browser + decodedPeaksCache.
  // Uses tts_audio_url (the /uploads/... URL), not tts_audio_path (an absolute
  // filesystem path that isn't fetchable from the browser).
  const versionedAudioPath = seg.tts_audio_url
    ? `${seg.tts_audio_url}?v=${seg.tts_duration_secs ?? 0}`
    : undefined

  // Native Browser Audio Playback — real audio only, no mock/simulation fallback
  const playAudio = () => {
    if (!seg.tts_audio_url) return
    const path = versionedAudioPath!
    const finalUrl = path.startsWith('/') ? path : `/${path}`
    const audio = new Audio(finalUrl)

    const key = seg.speaker_id ?? '__none__'
    const isMuted = mutedTrackIds[key]
    const hasSolo = Object.values(soloedTrackIds).some(Boolean)
    const isSoloed = soloedTrackIds[key]

    if (isMuted || (hasSolo && !isSoloed)) {
      audio.volume = 0
      console.log(`[Audio Playback] SILENCED segment ${seg.id} on track ${key} (Muted: ${isMuted}, Soloed: ${isSoloed})`)
    } else {
      audio.volume = volume
      console.log(`[Audio Playback] PLAYING segment ${seg.id} on track ${key} at volume ${volume}`)
    }

    audio.play().catch((e) => console.warn('Audio playback failed', e))
  }

  const handlePointerDown = (
    e: React.PointerEvent<HTMLDivElement>,
    actionType: 'drag' | 'resize-left' | 'resize-right'
  ) => {
    e.stopPropagation()
    const element = elementRef.current
    const container = scrollRef.current
    if (!element || !container) return

    element.setPointerCapture(e.pointerId)

    const startX = e.clientX
    const startY = e.clientY
    const startLeft = parseFloat(element.style.left) || timeToPixels(seg.start_time, zoom, PX_PER_SEC)
    const startWidth = parseFloat(element.style.width) || timeToPixels(seg.end_time - seg.start_time, zoom, PX_PER_SEC)

    // Snapshot for undo — the values before this gesture started.
    const beforeGesture: SegmentUpdate = {
      start_time: seg.start_time,
      end_time: seg.end_time,
      lane_index: seg.lane_index ?? 0,
    }

    const maxTimelineWidth = timeToPixels(duration || 60, zoom, PX_PER_SEC)

    // Group drag: dragging a clip that's part of the multi-selection moves
    // EVERY selected clip by the same time delta. Lanes stay unchanged in
    // group mode — only a solo drag can hop lanes.
    const selectedIds = useEditorStore.getState().selectedSegmentIds
    const isGroupDrag = actionType === 'drag' && selectedIds.length > 1 && selectedIds.includes(seg.id)
    const groupIdSet = new Set(isGroupDrag ? selectedIds : [seg.id])
    const groupMates = isGroupDrag
      ? allSegments.filter((s) => s.id !== seg.id && groupIdSet.has(s.id))
      : []
    const mateEls = groupMates
      .map((s) => [s, document.querySelector<HTMLElement>(`[data-seg-id="${s.id}"]`)] as const)
      .filter((pair): pair is readonly [Segment, HTMLElement] => !!pair[1])
    // The whole group must stay inside [0, timeline end]
    const groupMinLeftPx = groupMates.length
      ? Math.min(...groupMates.map((s) => timeToPixels(s.start_time, zoom, PX_PER_SEC)))
      : Infinity
    const groupMaxRightPx = groupMates.length
      ? Math.max(...groupMates.map((s) => timeToPixels(s.end_time, zoom, PX_PER_SEC)))
      : -Infinity

    // Magnetic snapping — CapCut-style: snap the moving edge(s) to any other
    // segment's start/end edge (any track) or the playhead, within a small
    // pixel threshold. Targets are computed once per gesture (not per-move).
    // Group members are excluded so the group never snaps to itself.
    const SNAP_THRESHOLD_PX = 8
    const snapTargetsPx = allSegments
      .filter((s) => !groupIdSet.has(s.id))
      .flatMap((s) => [
        timeToPixels(s.start_time, zoom, PX_PER_SEC),
        timeToPixels(s.end_time, zoom, PX_PER_SEC),
      ])
    snapTargetsPx.push(timeToPixels(useEditorStore.getState().currentTime, zoom, PX_PER_SEC))
    const snapValue = (px: number): number => {
      if (!snapEnabled) return px   // magnet toggled off in the toolbar
      let closest = px
      let closestDist = SNAP_THRESHOLD_PX
      for (const t of snapTargetsPx) {
        const d = Math.abs(t - px)
        if (d < closestDist) { closest = t; closestDist = d }
      }
      return closest
    }

    element.style.zIndex = '50'
    element.style.opacity = '0.9'

    // Drag never touches style.left during the gesture (see handlePointerMove) —
    // this tracks the last resolved left so pointer-up can commit it once.
    let lastDragLeft = startLeft

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const deltaX = moveEvent.clientX - startX
      const deltaY = moveEvent.clientY - startY
      let snapped = false

      if (actionType === 'drag') {
        let newLeft = startLeft + deltaX
        const rawLeft = newLeft
        const rawRight = newLeft + startWidth
        const snappedLeft = snapValue(rawLeft)
        const snappedRight = snapValue(rawRight)
        if (snappedLeft !== rawLeft) {
          newLeft = snappedLeft
          snapped = true
        } else if (snappedRight !== rawRight) {
          newLeft = snappedRight - startWidth
          snapped = true
        }
        newLeft = Math.max(0, Math.min(maxTimelineWidth - startWidth, newLeft))
        if (isGroupDrag) {
          // Clamp the shared delta so no group member leaves the timeline,
          // then slide every selected clip by the same amount. Y is locked —
          // group drags never hop lanes.
          const minDelta = -Math.min(startLeft, groupMinLeftPx)
          const maxDelta = maxTimelineWidth - Math.max(startLeft + startWidth, groupMaxRightPx)
          const delta = Math.max(minDelta, Math.min(maxDelta, newLeft - startLeft))
          newLeft = startLeft + delta
          lastDragLeft = newLeft
          element.style.transform = `translate(${delta}px, 0px)`
          for (const [, el] of mateEls) {
            el.style.transform = `translate(${delta}px, 0px)`
            el.style.opacity = '0.9'
            el.style.zIndex = '49'
          }
        } else {
          lastDragLeft = newLeft
          // Compositor-only transform — no layout reflow per frame, unlike
          // mutating `left` directly (was the cause of jumpy/laggy dragging).
          element.style.transform = `translate(${newLeft - startLeft}px, ${deltaY}px)`
        }
      } else if (actionType === 'resize-left') {
        const rightEdge = startLeft + startWidth
        let newLeft = startLeft + deltaX
        const snappedLeft = snapValue(newLeft)
        if (snappedLeft !== newLeft) { newLeft = snappedLeft; snapped = true }
        let newWidth = rightEdge - newLeft

        const minWidthPx = timeToPixels(0.1, zoom, PX_PER_SEC)
        if (newLeft < 0) {
          newWidth += newLeft
          newLeft = 0
        }
        if (newWidth < minWidthPx) {
          const diff = minWidthPx - newWidth
          newLeft -= diff
          newWidth = minWidthPx
        }
        element.style.left = `${newLeft}px`
        element.style.width = `${newWidth}px`
      } else if (actionType === 'resize-right') {
        const rawRight = startLeft + startWidth + deltaX
        const snappedRight = snapValue(rawRight)
        if (snappedRight !== rawRight) snapped = true
        let newWidth = snappedRight - startLeft

        const minWidthPx = timeToPixels(0.1, zoom, PX_PER_SEC)
        const maxWidthPx = maxTimelineWidth - startLeft
        newWidth = Math.max(minWidthPx, Math.min(maxWidthPx, newWidth))
        element.style.width = `${newWidth}px`
      }

      // Cheap, non-reactive visual cue when snapped — direct style mutation,
      // no React state, consistent with the rest of this drag implementation.
      element.style.boxShadow = snapped ? '0 0 0 2px rgba(167,139,250,0.9)' : ''
    }

    const handlePointerUp = (upEvent: PointerEvent) => {
      element.releasePointerCapture(upEvent.pointerId)
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)

      element.style.zIndex = ''
      element.style.opacity = ''
      element.style.boxShadow = ''
      if (actionType === 'drag') {
        // Commit the transform-tracked position to style.left once, so the
        // existing pixel-parsing logic below is untouched.
        element.style.left = `${lastDragLeft}px`
      }
      element.style.transform = ''
      for (const [, el] of mateEls) {
        el.style.transform = ''
        el.style.opacity = ''
        el.style.zIndex = ''
      }

      const finalLeft = parseFloat(element.style.left)
      const finalWidth = parseFloat(element.style.width)

      let newStartTime = pixelsToTime(finalLeft, zoom, PX_PER_SEC)
      let newEndTime = pixelsToTime(finalLeft + finalWidth, zoom, PX_PER_SEC)

      let finalLaneIndex = seg.lane_index ?? 0
      if (actionType === 'drag' && !isGroupDrag && lanesRef.current) {
        // Measure from the lanes container itself (not the outer scroll
        // container) so the BGM/Vocals/Speakers-legend rows above it — whose
        // combined height varies — never throw off which lane a drop lands on.
        const lanesRect = lanesRef.current.getBoundingClientRect()
        const relativeY = upEvent.clientY - lanesRect.top
        const trackIdx = Math.floor(relativeY / LANE_HEIGHT)
        const clampedIdx = Math.max(0, Math.min(laneIndices.length - 1, trackIdx))
        finalLaneIndex = laneIndices[clampedIdx]
      }

      // Prevent overlap with whatever's already on the destination lane —
      // real editors never let two clips on the same lane share time range.
      // In group mode, other group members don't count as obstacles: they
      // are moving by the same delta, so their OLD positions must not block.
      const neighbors = allSegments.filter(
        (s) => !groupIdSet.has(s.id) && (s.lane_index ?? 0) === finalLaneIndex
      )
      if (actionType === 'drag') {
        const dragDuration = newEndTime - newStartTime
        newStartTime = resolveDragOverlap(newStartTime, dragDuration, neighbors)
        newEndTime = newStartTime + dragDuration
      } else if (actionType === 'resize-left') {
        newStartTime = clampResizeLeft(newStartTime, newEndTime, neighbors)
      } else if (actionType === 'resize-right') {
        newEndTime = clampResizeRight(newEndTime, newStartTime, neighbors)
      }

      newStartTime = Math.round(newStartTime * 1000) / 1000
      newEndTime = Math.round(newEndTime * 1000) / 1000

      // Position/lane changes persist immediately — trimming or moving a
      // segment never touches its audio (matches real NLE trim behavior;
      // VideoPlayer's playback-rate auto-fit absorbs any duration mismatch).
      updateSegmentPosition(seg.id, newStartTime, newEndTime, finalLaneIndex)
      onUpdateBackend(seg.id, newStartTime, newEndTime, finalLaneIndex)

      const afterGesture: SegmentUpdate = { start_time: newStartTime, end_time: newEndTime, lane_index: finalLaneIndex }
      const changed = afterGesture.start_time !== beforeGesture.start_time
        || afterGesture.end_time !== beforeGesture.end_time
        || afterGesture.lane_index !== beforeGesture.lane_index

      if (isGroupDrag) {
        // Shift every other selected clip by the delta the grabbed clip
        // actually committed to, and record the whole move as ONE undo entry.
        if (!changed) return
        const deltaSecs = newStartTime - (beforeGesture.start_time ?? seg.start_time)
        const changes = [{ id: seg.id, before: beforeGesture, after: afterGesture }]
        for (const mate of groupMates) {
          const mateDuration = mate.end_time - mate.start_time
          const mateLane = mate.lane_index ?? 0
          const mateNeighbors = allSegments.filter(
            (s) => !groupIdSet.has(s.id) && (s.lane_index ?? 0) === mateLane
          )
          let mateStart = Math.max(0, mate.start_time + deltaSecs)
          mateStart = resolveDragOverlap(mateStart, mateDuration, mateNeighbors)
          mateStart = Math.round(mateStart * 1000) / 1000
          const mateEnd = Math.round((mateStart + mateDuration) * 1000) / 1000
          updateSegmentPosition(mate.id, mateStart, mateEnd, mateLane)
          onUpdateBackend(mate.id, mateStart, mateEnd, mateLane)
          changes.push({
            id: mate.id,
            before: { start_time: mate.start_time, end_time: mate.end_time, lane_index: mateLane },
            after: { start_time: mateStart, end_time: mateEnd, lane_index: mateLane },
          })
        }
        onRecordGroupHistory(changes)
      } else if (changed) {
        onRecordHistory(seg.id, beforeGesture, afterGesture)
      }
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
  }

  return (
    <div
      ref={elementRef}
      data-seg-id={seg.id}
      className={cn(
        'timeline-segment absolute top-[6px] bottom-[6px] select-none overflow-hidden transition-all',
        activeTool === 'blade' ? 'cursor-crosshair' : 'cursor-grab active:cursor-grabbing',
        isActive && 'active border-purple-500 outline-2 outline-purple-500 ring-2 ring-purple-500/20',
        isSplitting && 'opacity-60'
      )}
      style={{
        left,
        width,
        background: isApproved
          ? `linear-gradient(180deg, ${hexToRgba(color, 0.8)} 0%, ${hexToRgba(color, 0.95)} 100%)`
          : `linear-gradient(180deg, ${hexToRgba(color, 0.35)} 0%, ${hexToRgba(color, 0.45)} 100%)`,
        // The fit warning is a left-edge accent + a small icon, not a full red
        // box — clips must keep reading as their speaker's colour.
        borderLeft: `3px solid ${isTooFast ? '#F59E0B' : color}`,
        borderTop: `1px solid ${hexToRgba(color, isApproved ? 0.8 : 0.4)}`,
        borderRight: `1px solid ${hexToRgba(color, isApproved ? 0.6 : 0.3)}`,
        borderBottom: `1px solid ${hexToRgba(color, isApproved ? 0.6 : 0.3)}`,
        borderRadius: '8px',
        boxShadow: isActive
          ? `0 0 12px ${hexToRgba('#7C3AED', 0.6)}`
          : isSelected ? '0 0 8px rgba(167,139,250,0.35)' : 'none',
        outline: isActive
          ? '2px solid #7C3AED'
          : isSelected ? '2px solid rgba(167,139,250,0.85)' : 'none',
        outlineOffset: '-2px',
      }}
      onPointerDown={(e) => {
        if (e.button !== 0) return
        // Shift+click: toggle this clip in the multi-selection instead of
        // starting a drag (select tool only — blade keeps cutting).
        if (e.shiftKey && activeTool === 'select') {
          e.stopPropagation()
          onToggleSelect(seg.id)
          return
        }
        // Blade tool: a click cuts the clip where you clicked instead of
        // starting a drag — the clip's own rect gives the time directly.
        if (activeTool === 'blade') {
          e.stopPropagation()
          const rect = e.currentTarget.getBoundingClientRect()
          onSplit(seg, seg.start_time + pixelsToTime(e.clientX - rect.left, zoom, PX_PER_SEC))
          return
        }
        handlePointerDown(e, 'drag')
        // Grabbing a clip that's already part of the multi-selection must NOT
        // collapse the selection (onSelect clears it) — that press is the
        // start of a group drag. Plain click on an unselected clip keeps the
        // standard replace-selection behaviour.
        if (!isSelected) onSelect(seg)
      }}
      onMouseDown={(e) => {
        e.stopPropagation()
      }}
      onDoubleClick={(e) => {
        e.stopPropagation()
        playAudio()
      }}
      title={isTooFast
        ? `${seg.khmer_text || seg.source_text}\n\n⚠ This line needs ~${spokenSecs.toFixed(1)}s but the clip is ${clipSecs.toFixed(1)}s — it will be sped up. Lengthen the clip or shorten the text.`
        : (seg.khmer_text || seg.source_text)}
    >
      {/* Left Trim Handle — hidden while the blade tool is active so a click
          near the edge cuts the clip instead of silently trimming it */}
      {activeTool === 'select' && (
        <div
          className="absolute left-0 top-0 bottom-0 w-3 cursor-col-resize z-20 flex items-center justify-center group/left hover:bg-white/10 active:bg-white/20 transition-all"
          onPointerDown={(e) => {
            if (e.button === 0) {
              e.stopPropagation()
              handlePointerDown(e, 'resize-left')
            }
          }}
        >
          <div className="w-[2px] h-4 bg-white/30 group-hover/left:bg-white/80 group-active/left:bg-white rounded transition-colors" />
        </div>
      )}

      {/* Text band — its own strip across the top of the clip, with a dark
          scrim behind it. The waveform is confined below this band (see
          SegmentWaveform's positioning) so bars never run through the text;
          Khmer stacks subscripts under the baseline and became unreadable
          when the two overlapped. */}
      {width > 44 && (
        <div
          className={cn(
            // pl-4 clears the 12px trim handle so the speaker dot never sits
            // under its hover highlight
            'absolute left-0 right-0 top-0 h-[27px] flex items-center gap-1.5 pl-4 pointer-events-none select-none',
            'bg-gradient-to-b from-black/45 via-black/25 to-transparent',
            // Keep the text clear of whatever sits at the band's right edge
            isActive ? 'pr-14' : isApproved ? 'pr-7' : 'pr-4',
          )}
        >
          <span
            className="h-2 w-2 rounded-full shrink-0 ring-1 ring-black/40"
            style={{ background: color }}
            title="Speaker colour"
          />
          {isTooFast && (
            <AlertTriangle size={11} className="shrink-0 text-amber-400" />
          )}
          <span
            className="truncate text-[13px] font-semibold text-white leading-[1.7]"
            style={{ textShadow: '0 1px 3px rgba(0,0,0,0.9)' }}
          >
            {seg.khmer_text || seg.source_text}
          </span>
        </div>
      )}

      {/* Real audio waveform — decoded from the actual TTS clip, cache-busted
          so a re-synthesis of the same file path is never shown stale.
          Mounted only once the clip has scrolled into view. */}
      {versionedAudioPath && waveVisible && (
        <SegmentWaveform
          audioPath={versionedAudioPath}
          width={width}
        />
      )}

      {/* Regenerating indicator — reflects the real in-flight synth call */}
      {isRegenerating && (
        <div className="absolute inset-x-4 bottom-1.5 h-3 flex items-center justify-between pointer-events-none opacity-70">
          {[...Array(6)].map((_, i) => (
            <div
              key={i}
              className="w-1 bg-white/75 rounded-full animate-bounce"
              style={{
                height: i % 2 === 0 ? '70%' : '100%',
                animationDelay: `${i * 0.12}s`,
                animationDuration: '0.6s'
              }}
            />
          ))}
        </div>
      )}

      {/* Splitting — the two API calls are in flight */}
      {isSplitting && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/25 pointer-events-none">
          <Loader2 size={14} className="animate-spin text-white/90" />
        </div>
      )}

      {/* Persisting dot — reflects the real in-flight position/speaker PATCH */}
      {isPersisting && (
        <div className="absolute top-1 left-1 h-1.5 w-1.5 rounded-full bg-white/70 animate-pulse pointer-events-none" title="Saving…" />
      )}

      {/* Approved Indicator (hidden when segment is active — delete button takes that spot) */}
      {isApproved && !isActive && (
        <div
          className="absolute top-2 right-3.5 h-2 w-2 rounded-full bg-emerald-400 border border-black/30 shadow-sm pointer-events-none"
          title="Approved"
        />
      )}

      {/* Active-segment actions — Regenerate (real re-synthesis) + Delete */}
      {isActive && (
        <div className="absolute top-0.5 right-2.5 z-30 flex items-center gap-1">
          {seg.tts_audio_path && onRegenerate && (
            <button
              className="h-4 w-4 rounded-full bg-zinc-700 hover:bg-zinc-600 flex items-center justify-center shadow transition-colors disabled:opacity-50"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); onRegenerate(seg.id) }}
              disabled={isRegenerating}
              title="Regenerate audio"
            >
              <RefreshCw size={8} className={cn('text-white', isRegenerating && 'animate-spin')} strokeWidth={3} />
            </button>
          )}
          <button
            className="h-4 w-4 rounded-full bg-red-500 hover:bg-red-400 flex items-center justify-center shadow transition-colors"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onDelete(seg.id) }}
            title="Delete segment (Delete key)"
          >
            <X size={8} className="text-white" strokeWidth={3} />
          </button>
        </div>
      )}

      {/* Right Trim Handle — see note on the left handle */}
      {activeTool === 'select' && (
        <div
          className="absolute right-0 top-0 bottom-0 w-3 cursor-col-resize z-20 flex items-center justify-center group/right hover:bg-white/10 active:bg-white/20 transition-all"
          onPointerDown={(e) => {
            if (e.button === 0) {
              e.stopPropagation()
              handlePointerDown(e, 'resize-right')
            }
          }}
        >
          <div className="w-[2px] h-4 bg-white/30 group-hover/right:bg-white/80 group-active/right:bg-white rounded transition-colors" />
        </div>
      )}
    </div>
  )
})

// ── SVG Waveform Visualizer using Web Audio API ─────────────────────

const AUDIO_CONTEXT_KEY = '__shared_audio_context__'
function getSharedAudioContext() {
  if (typeof window === 'undefined') return null
  const w = window as Window & typeof globalThis & {
    __shared_audio_context__?: AudioContext
    webkitAudioContext?: typeof AudioContext
  }
  if (!w[AUDIO_CONTEXT_KEY]) {
    w[AUDIO_CONTEXT_KEY] = new (w.AudioContext || w.webkitAudioContext)()
  }
  return w[AUDIO_CONTEXT_KEY] as AudioContext
}

// High-resolution peaks, decoded once per audioPath — independent of zoom/width,
// so changing zoom never re-fetches or re-decodes audio (that was the cause of
// zoom-stutter: it used to re-fetch+re-decode every segment on every zoom tick).
const HIGH_RES_PEAKS = 300
const decodedPeaksCache: Record<string, number[]> = {}

// Cap concurrent waveform fetch+decodes — combined with the visibility gate
// above, opening a large project trickles loads instead of firing one HTTP
// request + Web Audio decode per clip simultaneously.
const MAX_CONCURRENT_WAVEFORM_LOADS = 6
let activeWaveformLoads = 0
const waveformLoadQueue: Array<() => void> = []

function acquireWaveformSlot(): Promise<() => void> {
  return new Promise((resolve) => {
    const grant = () => {
      activeWaveformLoads++
      let released = false
      resolve(() => {
        if (released) return
        released = true
        activeWaveformLoads--
        const next = waveformLoadQueue.shift()
        if (next) next()
      })
    }
    if (activeWaveformLoads < MAX_CONCURRENT_WAVEFORM_LOADS) grant()
    else waveformLoadQueue.push(grant)
  })
}

function extractPeaks(buffer: AudioBuffer, numPeaks = 60): number[] {
  const channelData = buffer.getChannelData(0)
  const step = Math.floor(channelData.length / numPeaks)
  const peaks: number[] = []

  for (let i = 0; i < numPeaks; i++) {
    const start = i * step
    const end = start + step
    let max = 0
    for (let j = start; j < end; j++) {
      const val = Math.abs(channelData[j])
      if (val > max) max = val
    }
    peaks.push(max)
  }

  const maxPeak = Math.max(...peaks, 0.01)
  return peaks.map((p) => p / maxPeak)
}

// Cheap synchronous resample of already-decoded peaks to fit the current
// display width — this is the only thing that runs on a zoom tick now.
function downsamplePeaks(peaks: number[], targetCount: number): number[] {
  if (targetCount >= peaks.length) return peaks
  const step = peaks.length / targetCount
  const out: number[] = []
  for (let i = 0; i < targetCount; i++) {
    const start = Math.floor(i * step)
    const end = Math.max(start + 1, Math.floor((i + 1) * step))
    let max = 0
    for (let j = start; j < end; j++) {
      if (peaks[j] > max) max = peaks[j]
    }
    out.push(max)
  }
  return out
}

interface SegmentWaveformProps {
  audioPath?: string
  width: number
  color?: string   // hex or CSS color — defaults to white (matches segment block)
  loading?: boolean // external loading hint (e.g. stem is being generated)
}

function SegmentWaveform({ audioPath, width, color = 'white', loading: externalLoading }: SegmentWaveformProps) {
  const [highResPeaks, setHighResPeaks] = useState<number[] | null>(() => {
    return (audioPath && decodedPeaksCache[audioPath]) || null
  })
  const [error,    setError]   = useState(false)
  const [fetching, setFetching] = useState(false)

  // Decode once per audioPath — deliberately NOT keyed on width, so zoom
  // changes never re-fetch or re-decode audio (only re-resample, below).
  useEffect(() => {
    let active = true

    if (!audioPath) {
      requestAnimationFrame(() => {
        if (active) {
          setHighResPeaks(null)
          setError(false)
        }
      })
      return () => {
        active = false
      }
    }
    if (decodedPeaksCache[audioPath]) {
      requestAnimationFrame(() => {
        if (active) {
          setHighResPeaks(decodedPeaksCache[audioPath])
          setError(false)
        }
      })
      return () => {
        active = false
      }
    }

    const fetchAndDecode = async () => {
      if (active) {
        setFetching(true)
        setError(false)
      }
      const release = await acquireWaveformSlot()
      if (!active) { release(); return }
      try {
        const url = audioPath.startsWith('/') ? audioPath : `/${audioPath}`
        const res = await fetch(url)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const arrayBuffer = await res.arrayBuffer()
        const ctx = getSharedAudioContext()
        if (!ctx) return

        // decodeAudioData can throw on corrupt/unsupported files
        const audioBuffer = await ctx.decodeAudioData(arrayBuffer)
        const extracted = extractPeaks(audioBuffer, HIGH_RES_PEAKS)

        decodedPeaksCache[audioPath] = extracted
        if (active) { setHighResPeaks(extracted); setFetching(false) }
      } catch (err) {
        console.warn('Waveform load failed for', audioPath, err)
        if (active) { setError(true); setFetching(false) }
      } finally {
        release()
      }
    }

    fetchAndDecode()
    return () => { active = false }
  }, [audioPath])

  // Cheap synchronous resample to the current display width — this is all
  // that runs on a zoom tick now, no network/decode involved.
  const numPeaks = Math.max(20, Math.floor(width / 3.5))
  const peaks = useMemo(
    () => (highResPeaks ? downsamplePeaks(highResPeaks, numPeaks) : null),
    [highResPeaks, numPeaks]
  )

  const isLoading = externalLoading || (fetching && !peaks && !error)

  // Loading state — pulsing bars so users know it's working
  if (isLoading) {
    return (
      <div className="absolute inset-x-3 bottom-[3px] h-[21px] flex items-end gap-[2px] pointer-events-none">
        {[0.4, 0.7, 0.5, 0.9, 0.6, 0.8, 0.45, 0.75, 0.55, 0.85, 0.5, 0.7].map((h, i) => (
          <div
            key={i}
            className="flex-1 rounded-sm animate-pulse"
            style={{ height: `${h * 100}%`, background: color, opacity: 0.25, animationDelay: `${i * 0.08}s` }}
          />
        ))}
      </div>
    )
  }

  // Error or still null — faint dashed line in the waveform band
  if (error || !peaks) {
    return (
      <div className="absolute inset-x-2.5 bottom-[3px] h-[21px] flex items-center justify-center opacity-[0.12] pointer-events-none">
        <div className="w-full border-t border-dashed" style={{ borderColor: color }} />
      </div>
    )
  }

  return (
    // Anchored to the bottom of the clip, clear of the text band above it.
    <svg
      className="absolute inset-x-2.5 bottom-[3px] h-[21px] w-[calc(100%-20px)] pointer-events-none opacity-[0.85]"
      preserveAspectRatio="none"
      viewBox={`0 0 ${peaks.length} 1`}
    >
      {peaks.map((peak, i) => {
        // Floor the bar height so quiet passages still read as audio rather
        // than vanishing into the clip background.
        const barHeight = Math.max(0.14, peak * 0.92)
        const y = (1 - barHeight) / 2
        return (
          <rect
            key={i}
            x={i}
            y={y}
            width={0.72}
            height={barHeight}
            fill={color}
            rx={0.18}
          />
        )
      })}
    </svg>
  )
}

// ── WaveSurfer Stem Waveform ──────────────────────────────────────────

interface StemWaveformProps {
  audioUrl: string
  color: string
  pxPerSec: number
}

function StemWaveform({ audioUrl, color, pxPerSec }: StemWaveformProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wsRef = useRef<WaveSurfer | null>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState(false)
  // Bumped to force a fresh WaveSurfer instance when the user hits Retry
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // Guards this instance's async callbacks against its own teardown.
    // StrictMode runs every effect mount → cleanup → mount in dev, and
    // `ws.destroy()` aborts the in-flight fetch, which WaveSurfer reports by
    // emitting `error`. Without this flag that abort set `error` on state
    // shared with the *replacement* instance, so a stem that actually loaded
    // fine still rendered as an empty track forever (the render checks `error`
    // before `ready`). Which stem lost the race was pure timing — hence BGM
    // blank while Vocals drew normally.
    let cancelled = false

    setReady(false)
    setError(false)

    const url = audioUrl.startsWith('/') ? audioUrl : `/${audioUrl}`

    const ws = WaveSurfer.create({
      container,
      url,
      waveColor: color,
      progressColor: color,
      height: 46,        // taller stem waveform — the row grew to STEM_HEIGHT
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      interact: false,
      normalize: true,
      hideScrollbar: true,
      minPxPerSec: pxPerSec,
    })

    ws.on('ready', () => {
      if (cancelled) return
      setReady(true)
      setError(false)
    })
    ws.on('error', (err) => {
      if (cancelled) return   // teardown abort, not a real load failure
      console.warn(`[StemWaveform] failed to load ${url}`, err)
      setError(true)
    })
    wsRef.current = ws

    return () => { cancelled = true; ws.destroy(); wsRef.current = null }
  }, [audioUrl, color, attempt])

  // Sync zoom level without recreating the instance.
  // Guarded: WaveSurfer.zoom() throws "No audio loaded" if the buffer isn't
  // decoded yet (can happen on a remount race when returning to the editor).
  // A waveform zoom hiccup must never crash the whole editor.
  useEffect(() => {
    const ws = wsRef.current
    if (!ws || !ready) return
    try {
      if (ws.getDecodedData()) ws.zoom(pxPerSec)
    } catch {
      /* audio not decoded yet — the next ready/zoom cycle will apply it */
    }
  }, [pxPerSec, ready])

  // A failed stem used to render as a 10%-opacity dashed line, i.e. an empty
  // track — visually identical to "this stem has no sound". Say so instead,
  // and offer a retry (decoding can lose a race when several stems load at once).
  if (error) {
    return (
      <div className="absolute inset-0 flex items-center justify-center gap-2 px-3">
        <span className="text-[11px] text-amber-400/80 truncate">Waveform failed to load</span>
        <button
          onClick={(e) => { e.stopPropagation(); setAttempt((a) => a + 1) }}
          onPointerDown={(e) => e.stopPropagation()}
          className="shrink-0 flex items-center gap-1 px-2 h-6 rounded-md bg-white/10 hover:bg-white/20 text-[10.5px] font-medium text-white/80 transition-colors"
        >
          <RefreshCw size={10} /> Retry
        </button>
      </div>
    )
  }

  return (
    <>
      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <Loader2 size={11} className="animate-spin" style={{ color, opacity: 0.4 }} />
        </div>
      )}
      {/* The centring flex lives on the OUTER box: WaveSurfer's shadow host
          only sets `min-width: 1px`, so as a direct flex item it collapses to
          nothing and no waveform is drawn. The inner w-full div gives the flex
          layout a definite width, and WaveSurfer's host then fills it as a
          normal block child. */}
      <div
        className="absolute inset-0 flex items-center overflow-hidden"
        style={{ opacity: ready ? 0.95 : 0 }}
      >
        <div ref={containerRef} className="w-full" />
      </div>
    </>
  )
}
