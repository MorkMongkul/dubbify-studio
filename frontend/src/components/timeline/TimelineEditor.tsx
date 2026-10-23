// src/components/timeline/TimelineEditor.tsx
import { useRef, useCallback, useEffect, useState, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import WaveSurfer from 'wavesurfer.js'
import {
  ZoomIn, ZoomOut, Maximize2, Loader2, Scissors, X, RefreshCw,
  MousePointer2, SplitSquareHorizontal, Trash2, Magnet, Wand2, Layers, ChevronDown, Undo2, Redo2,
  ArrowLeftToLine, ArrowRightToLine, Gauge, AudioLines, Music, Volume2, VolumeX, Headphones, HeadphoneOff,
  ArrowDownWideNarrow, type LucideIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import type { Segment, SegmentUpdate, Speaker, Job, LayoutChange } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { useDeleteSegment, useSynthesizeSegment, useAutofitSegments, useTidyLanes, useSetBgmVolume, useLayoutSegments, useSplitSegment, useDuplicateSegment, useSetDucking, putSegments } from '@/hooks/useApi'
import { useScrubAudio } from '@/hooks/useScrubAudio'
import { segments as segmentsApi } from '@/api/client'
import { timeToPixels, pixelsToTime, formatTime, getSpeakerColor, cn, isJobRunning, originalVoicesReplaced } from '@/lib/utils'
import { recordDuckingChange, recordLayoutChange, recordSegmentChange, recordSegmentDelete, recordSegmentsChange, recordSegmentsDelete, recordSegmentDuplicate, recordSegmentSplit } from '@/lib/historyHelpers'
import { Tooltip } from '@/components/ui/Tooltip'
import { InteractiveSegment, LANE_HEIGHT } from './TimelineClip'
import { clipAtPlayhead, trackRows } from '@/lib/tracks'
import { effectiveSpeed, isVoiced, overrunSpans, trimEnd, trimStart } from '@/lib/clipRules'
import { isTypingTarget } from '@/lib/transport'
import { redoWithToast, undoWithToast } from '@/lib/undoActions'
import { useHistoryStore } from '@/store/historyStore'
import { ContextMenu, type MenuItem } from '@/components/ui/ContextMenu'

const PX_PER_SEC = 100 // base pixels per second at zoom=1
const STEM_HEIGHT = 64  // BGM / Vocals stem rows
// The speeds the right-click menu offers
const SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.2, 1.25, 1.5]

// A toolbar button: an icon, its name and shortcut in the tooltip
function ToolButton({ label, icon: Icon, onClick, disabled, pressed, danger, busy }: {
  label: string
  icon: LucideIcon
  onClick: () => void
  disabled?: boolean
  pressed?: boolean
  danger?: boolean
  busy?: boolean
}) {
  return (
    <Tooltip content={label} side="bottom">
      <button
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        aria-pressed={pressed}
        className={cn(
          'h-7 w-7 rounded-md flex items-center justify-center transition-colors disabled:opacity-25 disabled:hover:bg-transparent',
          pressed ? 'bg-brand/20 text-brand-200'
            : danger ? 'text-white/50 hover:text-red-400 hover:bg-red-500/15'
            : 'text-white/50 hover:text-white hover:bg-white/8',
        )}
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Icon size={14} />}
      </button>
    </Tooltip>
  )
}
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
  // Once analysis has run the dubbed voices replace the original ones: export
  // leaves them out, so they're muted here by default too (M unmutes to compare).
  const vocalsReplaced = originalVoicesReplaced(job?.status)

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
  const vocalsMuted       = mutedTrackIds['__vocals__'] ?? vocalsReplaced
  const soloedTrackIds    = useEditorStore((s) => s.soloedTrackIds)
  const timelineHeight    = useEditorStore((s) => s.timelineHeight)
  const speakerPanelWidth = useEditorStore((s) => s.speakerPanelWidth)
  const setCurrentTime    = useEditorStore((s) => s.setCurrentTime)
  const setActiveSegment  = useEditorStore((s) => s.setActiveSegment)
  const zoomIn            = useEditorStore((s) => s.zoomIn)
  const zoomOut           = useEditorStore((s) => s.zoomOut)
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

  const { mutate: layoutSegments, isPending: isLayingOut, variables: layoutVariables } = useLayoutSegments()
  const { mutate: splitSegment, isPending: isSplittingClip, variables: splitVariables } = useSplitSegment()
  const { mutate: duplicateSegment } = useDuplicateSegment()
  const { mutate: deleteSegment } = useDeleteSegment()
  const { mutate: regenerateSegment, isPending: isRegenerating, variables: regenVariables } = useSynthesizeSegment()
  const autofitSegments = useAutofitSegments()
  const tidyLanes = useTidyLanes()
  const { mutate: setBgmVolume, isPending: isSettingBgmVolume } = useSetBgmVolume()
  const qc = useQueryClient()

  // BGM & SFX gain — mirrors job.bgm_volume_db while dragging, committed on
  // release (see handleVolumeChange). Re-synced whenever the confirmed
  // server value changes (job switch, or the mutation's own response).
  const serverBgmVolume = job?.bgm_volume_db ?? 0
  const [bgmVolume, setBgmVolumeLocal] = useState<number>(serverBgmVolume)
  // Re-sync during render when the confirmed value or the job changes (React's
  // "adjust state when a prop changes" pattern — no extra effect pass).
  const [syncedBgm, setSyncedBgm] = useState({ jobId: job?.id, value: serverBgmVolume })
  if (syncedBgm.jobId !== job?.id || syncedBgm.value !== serverBgmVolume) {
    setSyncedBgm({ jobId: job?.id, value: serverBgmVolume })
    setBgmVolumeLocal(serverBgmVolume)
  }
  // The music dips under the voices — one undo step
  const { mutate: setDucking, isPending: isSettingDucking } = useSetDucking()
  const handleToggleDucking = useCallback(() => {
    if (!jobId) return
    const before = !!job?.duck_music
    setDucking({ jobId, on: !before }, {
      onSuccess: () => recordDuckingChange(qc, jobId, before, !before),
      onError: () => toast.error("Couldn't change the music dip"),
    })
  }, [jobId, job?.duck_music, setDucking, qc])
  const scrubAudio = useEditorStore((s) => s.scrubAudio)
  const toggleScrubAudio = useEditorStore((s) => s.toggleScrubAudio)

  const handleBgmVolumeCommit = useCallback((value: number) => {
    if (!jobId || value === (job?.bgm_volume_db ?? 0)) return
    setBgmVolume({ jobId, volumeDb: value }, {
      onError: () => {
        toast.error('Failed to update BGM volume')
        setBgmVolumeLocal(job?.bgm_volume_db ?? 0)
      },
    })
  }, [jobId, job?.bgm_volume_db, setBgmVolume])

  // Auto-fit: one undo step puts every voice back at the speed (and on the
  // track) it had — the server re-renders them from their raw voices.
  const handleAutofit = () => {
    if (!jobId) return
    const before = new Map(segmentsRef.current.map((s) => [s.id, s]))
    autofitSegments.mutate(jobId, {
      onSuccess: async ({ fitted, left }) => {
        if (fitted) toast.success(`Sped up ${fitted} voice${fitted === 1 ? '' : 's'}`)
        if (left) toast.warning(`${left} voice${left === 1 ? '' : 's'} still run${left === 1 ? 's' : ''} into the next line`)
        if (!fitted && !left) toast.info('No voice runs into the next line')
        if (!fitted) return
        const after = await segmentsApi.listByJob(jobId)
        const speedOf = (s: Segment) => Math.round(effectiveSpeed(s) * 1000) / 1000
        const changes = after.flatMap((s) => {
          const was = before.get(s.id)
          if (!was || (speedOf(was) === speedOf(s) && (was.lane_index ?? 0) === (s.lane_index ?? 0))) return []
          return [{
            id: s.id,
            before: { voice_speed: speedOf(was), lane_index: was.lane_index ?? 0 },
            after: { voice_speed: speedOf(s), lane_index: s.lane_index ?? 0 },
          }]
        })
        if (changes.length) recordSegmentsChange(qc, jobId, changes, 'Auto-fit')
      },
      onError: (err: unknown) => {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        toast.error(detail ?? 'Auto-fit failed')
      },
    })
  }

  // Tidy: one undo step puts every clip back on its track
  const handleTidyLanes = () => {
    if (!jobId) return
    const before = new Map(segmentsRef.current.map((s) => [s.id, s.lane_index ?? 0]))
    tidyLanes.mutate(jobId, {
      onSuccess: async ({ changed, lanes }) => {
        toast.success(changed === 0
          ? 'Tracks already tidy'
          : `Repacked ${changed} clip(s) into ${lanes} track(s)`)
        if (!changed) return
        const after = await segmentsApi.listByJob(jobId)
        recordLayoutChange(qc, jobId, after
          .filter((s) => before.has(s.id) && before.get(s.id) !== (s.lane_index ?? 0))
          .map((s) => ({ id: s.id, before: { id: s.id, lane_index: before.get(s.id)! }, after: { id: s.id, lane_index: s.lane_index ?? 0 } })),
          'Tidy tracks')
      },
      onError: () => toast.error('Tidy failed'),
    })
  }

  // Removes the multi-selection when one exists (a single undo entry restores
  // all of it), else the active clip. Used by Delete/Backspace and the trash
  // button. Returns whether anything was deleted.
  const deleteSelection = useCallback((): boolean => {
    if (!jobId) return false
    const ids = useEditorStore.getState().selectedSegmentIds
    if (ids.length > 0) {
      const targets = segments.filter((s) => ids.includes(s.id))
      if (targets.length === 0) return false
      Promise.all(targets.map((t) => segmentsApi.delete(t.id)))
        .then(() => qc.invalidateQueries({ queryKey: ['segments', jobId] }))
        .catch(() => {
          toast.error('Failed to delete some clips')
          qc.invalidateQueries({ queryKey: ['segments', jobId] })
        })
      recordSegmentsDelete(qc, jobId, targets)
      useEditorStore.setState({ selectedSegmentIds: [], activeSegmentId: null })
      toast.success(`Deleted ${targets.length} clips`)
      return true
    }
    if (!activeSegmentId) return false
    const seg = segments.find((s) => s.id === activeSegmentId)
    deleteSegment({ segmentId: activeSegmentId, jobId })
    if (seg) recordSegmentDelete(qc, jobId, seg)
    setActiveSegment(null)
    return true
  }, [activeSegmentId, jobId, deleteSegment, setActiveSegment, segments, qc])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return
      if (isTypingTarget(e.target) || (e.target as HTMLElement)?.tagName === 'AUDIO') return
      if (useEditorStore.getState().settingsOpen) return
      if (deleteSelection()) e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [deleteSelection])

  const totalWidth = Math.max(timeToPixels(duration || 60, zoom, PX_PER_SEC), 800)

  // Stem waveform paths — use tiny 8kHz mono preview files so Web Audio decodes instantly.
  // Full stems (~19MB) are kept for playback; preview files (~1.7MB) are for visualization only.
  // Cache-busted by bgm_volume_db — the backend overwrites this same file path
  // whenever the BGM gain changes, so the URL needs to change too or the
  // browser (and WaveSurfer's decode) keeps showing the pre-gain waveform.
  const noVocalsPreviewPath = jobId && projectId
    ? `uploads/${projectId}/${jobId}/no_vocals.preview.wav?v=${job?.bgm_volume_db ?? 0}-${job?.stems_version ?? 0}`
    : null
  const vocalsPreviewPath   = jobId && projectId
    ? `uploads/${projectId}/${jobId}/vocals.preview.wav?v=${job?.stems_version ?? 0}`
    : null

  // Timeline rows are free lanes, independent of speaker. Only lanes that hold
  // a clip are drawn (an emptied track disappears, like CapCut's); a drop above,
  // below or between tracks makes a new one (lib/tracks.ts).
  const rows = useMemo(() => trackRows(segments), [segments])
  // The part of each voice that runs into a later line where the original didn't
  const overruns = useMemo(() => overrunSpans(segments), [segments])
  // Where a drop would insert a new track — positioned by the dragged clip
  const insertLineRef = useRef<HTMLDivElement>(null)

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
  // A drag or trim: one layout request, then one undo step holding every clip
  // whose place changed — including clips the server moved to another track.
  const handleCommitLayout = useCallback((changes: LayoutChange[], label: string) => {
    if (!jobId || !changes.length) return
    const before = new Map(segmentsRef.current.map((s) => [s.id, s]))
    const place = (s: Segment): LayoutChange => ({
      id: s.id, start_time: s.start_time, end_time: s.end_time, lane_index: s.lane_index ?? 0, voice_offset: s.voice_offset ?? 0,
    })
    layoutSegments({ jobId, changes }, {
      onSuccess: (updated) => {
        const moved = updated.filter((u) => before.has(u.id))
        recordLayoutChange(qc, jobId, moved.map((u) => ({ id: u.id, before: place(before.get(u.id)!), after: place(u) })), label)
      },
      onError: () => toast.error("Couldn't move the clip"),
    })
  }, [jobId, layoutSegments, qc])

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
  // Cuts one clip into two at `atTime` on the server: a voiced clip's halves
  // each play their own part of the one voice; the text is split at the
  // nearest phrase break (routes/segments.py split_segment). One undo step.
  const splittingId = isSplittingClip ? splitVariables?.segmentId ?? null : null
  const handleSplit = useCallback((seg: Segment, atTime: number) => {
    if (!jobId || isSplittingClip) return
    // Reached from both the blade tool and the Split button, so keep the
    // message about the cut point rather than naming one of them.
    if (atTime <= seg.start_time + MIN_CLIP_SECS || atTime >= seg.end_time - MIN_CLIP_SECS) {
      toast.error('Cut point is too close to the clip edge')
      return
    }
    const at = Math.round(atTime * 1000) / 1000
    splitSegment({ segmentId: seg.id, at, jobId }, {
      onSuccess: (result) => {
        recordSegmentSplit(qc, jobId, seg, result, at)
        setActiveSegment(result.second.id)
      },
      onError: () => toast.error('Failed to split clip'),
    })
  }, [jobId, qc, splitSegment, isSplittingClip, setActiveSegment])

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
  // A copy right after the clip, with its own copy of the voice (server side).
  const handleDuplicate = useCallback(() => {
    if (!jobId || !activeSegmentId) return
    const sourceId = activeSegmentId
    duplicateSegment({ segmentId: sourceId, jobId }, {
      onSuccess: (created) => {
        recordSegmentDuplicate(qc, jobId, created, sourceId)
        setActiveSegment(created.id)
      },
      onError: () => toast.error('Failed to duplicate clip'),
    })
  }, [jobId, activeSegmentId, qc, duplicateSegment, setActiveSegment])

  // ── Trim at the playhead (Q / W, the toolbar, the right-click menu) ────
  // Cuts sound from the voice's start / end like dragging the edge; a clip
  // with no voice follows its text and has nothing to cut.
  const trimAt = useCallback((seg: Segment, t: number, side: 'start' | 'end') => {
    if (!isVoiced(seg)) {
      toast.info('Make a voice first — this line follows its text')
      return
    }
    if (side === 'start') {
      const r = trimStart(seg, t)
      if (Math.abs(r.start_time - seg.start_time) > 1e-6) {
        handleCommitLayout([{ id: seg.id, start_time: r.start_time, voice_offset: r.voice_offset }], 'Trim start')
      }
    } else {
      const r = trimEnd(seg, t)
      if (Math.abs(r.end_time - seg.end_time) > 1e-6) handleCommitLayout([{ id: seg.id, ...r }], 'Trim end')
    }
  }, [handleCommitLayout])

  const handleTrimAtPlayhead = useCallback((side: 'start' | 'end') => {
    const t = useEditorStore.getState().currentTime
    const placed = clipAtPlayhead(segmentsRef.current, t, useEditorStore.getState().activeSegmentId)
    const seg = placed && segmentsRef.current.find((s) => s.id === placed.id)
    if (!seg) {
      toast.error('Move the playhead over a clip to trim it')
      return
    }
    trimAt(seg, t, side)
  }, [trimAt])

  // A speed for the clip's voice (the right-click menu): its length follows
  const handleSetSpeed = useCallback(async (seg: Segment, speed: number) => {
    if (!jobId) return
    const was = Math.round(effectiveSpeed(seg) * 1000) / 1000
    if (Math.abs(was - speed) < 0.0005) return
    try {
      const updated = await segmentsApi.update(seg.id, { voice_speed: speed })
      putSegments(qc, jobId, [updated])
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      recordSegmentChange(qc, jobId, seg.id,
        { voice_speed: was, lane_index: seg.lane_index ?? 0 },
        { voice_speed: speed, lane_index: updated.lane_index ?? 0 },
        'Change voice speed')
    } catch {
      toast.error('Failed to update voice speed')
    }
  }, [jobId, qc])

  // A setting changed on the clip itself (fades, the volume line): one PATCH, one undo step
  const handleClipPatch = useCallback(async (seg: Segment, after: SegmentUpdate, before: SegmentUpdate, label: string) => {
    if (!jobId) return
    try {
      const updated = await segmentsApi.update(seg.id, after)
      putSegments(qc, jobId, [updated])
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      recordSegmentChange(qc, jobId, seg.id, before, after, label)
    } catch {
      toast.error(`Couldn't save: ${label.toLowerCase()}`)
    }
  }, [jobId, qc])

  // ── Right-click on a clip ───────────────────────────────────────────────
  const [clipMenu, setClipMenu] = useState<{ seg: Segment; x: number; y: number; at: number } | null>(null)
  const closeClipMenu = useCallback(() => setClipMenu(null), [])
  const handleClipContextMenu = useCallback((seg: Segment, x: number, y: number) => {
    // Select it, as a left click would
    setActiveSegment(seg.id)
    setInspectorMode('audio_clip_settings')
    setFocusedTimelineItemId(seg.id)
    // The playhead when it's over the clip, else where the clip was clicked
    const t = useEditorStore.getState().currentTime
    const content = scrollRef.current
    const clicked = content
      ? pixelsToTime(x - content.getBoundingClientRect().left + content.scrollLeft - speakerPanelWidth, zoom, PX_PER_SEC)
      : t
    const at = t > seg.start_time && t < seg.end_time ? t : clicked
    setClipMenu({ seg, x, y, at })
  }, [setActiveSegment, setInspectorMode, setFocusedTimelineItemId, speakerPanelWidth, zoom])

  const clipMenuItems = (menu: { seg: Segment; at: number }): MenuItem[] => {
    const { seg, at } = menu
    const voiced = isVoiced(seg)
    const current = Math.round(effectiveSpeed(seg) * 100) / 100
    return [
      { icon: SplitSquareHorizontal, label: 'Split', shortcut: 'S', onSelect: () => handleSplit(seg, at) },
      { icon: ArrowLeftToLine, label: 'Trim start', shortcut: 'Q', disabled: !voiced, onSelect: () => trimAt(seg, at, 'start') },
      { icon: ArrowRightToLine, label: 'Trim end', shortcut: 'W', disabled: !voiced, onSelect: () => trimAt(seg, at, 'end') },
      'separator',
      { icon: AudioLines, label: 'New voice', disabled: !jobId || !(seg.khmer_text ?? '').trim(), onSelect: () => handleSegmentRegenerate(seg.id) },
      {
        icon: Gauge, label: 'Speed', disabled: !voiced,
        items: SPEEDS.map((v) => ({
          icon: Gauge, label: `${v}×`, checked: Math.abs(current - v) < 0.005, onSelect: () => handleSetSpeed(seg, v),
        })),
      },
      'separator',
      { icon: Trash2, label: 'Delete', shortcut: '⌫', onSelect: () => handleSegmentDelete(seg.id) },
    ]
  }

  // The tool picker under the select tool's button
  const [toolMenu, setToolMenu] = useState<{ x: number; y: number } | null>(null)
  const closeToolMenu = useCallback(() => setToolMenu(null), [])

  // Undo / redo buttons follow the history
  const canUndo = useHistoryStore((s) => s.past.length > 0)
  const canRedo = useHistoryStore((s) => s.future.length > 0)

  // ── Tool shortcuts (V select · C blade · S split · ⌘D duplicate) ────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || (e.target as HTMLElement)?.tagName === 'AUDIO') return
      if (useEditorStore.getState().settingsOpen) return
      const key = e.key.toLowerCase()
      if ((e.metaKey || e.ctrlKey) && key === 'd') { e.preventDefault(); handleDuplicate(); return }
      // ⌘B / Ctrl+B — the split shortcut CapCut users already have in muscle memory
      if ((e.metaKey || e.ctrlKey) && key === 'b') { e.preventDefault(); handleSplitAtPlayhead(); return }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (key === 'v') setActiveTool('select')
      else if (key === 'c') setActiveTool('blade')
      else if (key === 's') { e.preventDefault(); handleSplitAtPlayhead() }
      else if (key === 'q') { e.preventDefault(); handleTrimAtPlayhead('start') }
      else if (key === 'w') { e.preventDefault(); handleTrimAtPlayhead('end') }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setActiveTool, handleSplitAtPlayhead, handleDuplicate, handleTrimAtPlayhead])

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
  // Hear while dragging: a snippet of the mix wherever the playhead is dragged to
  const musicUrl = jobId && projectId && job?.stems_separated != null
    ? `/uploads/${projectId}/${jobId}/no_vocals.m4a?v=${job?.bgm_volume_db ?? 0}-${job?.stems_version ?? 0}`
    : null
  const scrubSnippet = useScrubAudio({ segmentsRef, musicUrl, duckMusic: !!job?.duck_music })
  const scrubTo = useCallback((t: number) => {
    const clamped = Math.max(0, Math.min(duration, t))
    setCurrentTime(clamped)
    const st = useEditorStore.getState()
    if (st.scrubAudio && !st.isPlaying) scrubSnippet(clamped)
  }, [duration, setCurrentTime, scrubSnippet])

  const seekFromEvent = useCallback((clientX: number, rect: DOMRect) => {
    const scrollLeft = scrollRef.current?.scrollLeft ?? 0
    const x = clientX - rect.left + scrollLeft
    scrubTo(pixelsToTime(x, zoom, PX_PER_SEC))
  }, [zoom, scrubTo])

  const onRulerMouseDown = useCallback((e: React.MouseEvent) => {
    isDraggingRef.current = true
    // Captured now: React clears e.currentTarget once this handler returns, so
    // reading it inside the move listener threw on every mousemove.
    const ruler = e.currentTarget as HTMLElement
    seekFromEvent(e.clientX, ruler.getBoundingClientRect())
    const onMove  = (ev: MouseEvent) => seekFromEvent(ev.clientX, ruler.getBoundingClientRect())
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
    scrubTo(pixelsToTime(x, zoom, PX_PER_SEC))
  }, [zoom, speakerPanelWidth, scrubTo])

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

    // Clip hit-boxes in content space, by the row each clip is drawn in
    // (empty tracks aren't drawn, so a lane number is not a row).
    const rowOf = (s: Segment) => rows.indexOf(s.lane_index ?? 0)
    const boxes = canMarquee ? segmentsRef.current.map((s) => ({
      id: s.id,
      x1: speakerPanelWidth + timeToPixels(s.start_time, zoom, PX_PER_SEC),
      x2: speakerPanelWidth + timeToPixels(s.end_time, zoom, PX_PER_SEC),
      y1: lanesTop + rowOf(s) * LANE_HEIGHT,
      y2: lanesTop + (rowOf(s) + 1) * LANE_HEIGHT,
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
  }, [seekFromTrackEvent, speakerPanelWidth, zoom, activeTool, setSelectedSegments, rows])

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
      {/* Toolbar — icons only; each tool's name and shortcut in its tooltip */}
      <div className="flex items-center justify-between gap-3 px-3 border-b border-white/[0.06] shrink-0 bg-ink-900 select-none w-full relative h-11">
        {/* Left: editing tools */}
        <div className="flex items-center gap-1 z-10">
          <Tooltip content={activeTool === 'select' ? 'Select (V)' : 'Blade (C)'} side="bottom">
            <button
              onClick={(e) => {
                const r = e.currentTarget.getBoundingClientRect()
                setToolMenu({ x: r.left, y: r.bottom + 4 })
              }}
              aria-label="Tool"
              className="h-7 pl-2 pr-1 rounded-md flex items-center gap-0.5 bg-brand/20 text-brand-200 hover:bg-brand/30 transition-colors"
            >
              {activeTool === 'select' ? <MousePointer2 size={14} /> : <Scissors size={14} />}
              <ChevronDown size={11} className="opacity-70" />
            </button>
          </Tooltip>
          {toolMenu && (
            <ContextMenu x={toolMenu.x} y={toolMenu.y} onClose={closeToolMenu} items={[
              { icon: MousePointer2, label: 'Select', shortcut: 'V', checked: activeTool === 'select', onSelect: () => setActiveTool('select') },
              { icon: Scissors, label: 'Blade', shortcut: 'C', checked: activeTool === 'blade', onSelect: () => setActiveTool('blade') },
            ]} />
          )}
          <ToolButton label="Undo (⌘Z)" icon={Undo2} onClick={() => { undoWithToast() }} disabled={!canUndo} />
          <ToolButton label="Redo (⇧⌘Z)" icon={Redo2} onClick={() => { redoWithToast() }} disabled={!canRedo} />

          <div className="w-px h-5 bg-white/[0.08] mx-1" />

          <ToolButton label="Split (S)" icon={SplitSquareHorizontal} onClick={handleSplitAtPlayhead} />
          <ToolButton label="Trim start (Q)" icon={ArrowLeftToLine} onClick={() => handleTrimAtPlayhead('start')} />
          <ToolButton label="Trim end (W)" icon={ArrowRightToLine} onClick={() => handleTrimAtPlayhead('end')} />
          <ToolButton label="Delete (⌫)" icon={Trash2} onClick={deleteSelection} danger
            disabled={!activeSegmentId && selectedSegmentIds.length === 0} />

          <div className="w-px h-5 bg-white/[0.08] mx-1" />

          <ToolButton label="Auto-fit: speed up voices that run into the next line" icon={Wand2}
            busy={autofitSegments.isPending} onClick={handleAutofit} disabled={!jobId || autofitSegments.isPending} />
          <ToolButton label="Tidy tracks" icon={Layers}
            busy={tidyLanes.isPending} onClick={handleTidyLanes} disabled={!jobId || tidyLanes.isPending} />

          {/* Multi-selection count — marquee/Shift+click feed the same
              selection the transcript's batch toolbar acts on */}
          {selectedSegmentIds.length > 0 && (
            <>
              <div className="w-px h-5 bg-white/[0.08] mx-1" />
              <span className="text-[11px] font-semibold text-brand-300 tabular-nums select-none" title="Selected">
                {selectedSegmentIds.length}
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

        {/* Right: snapping, zoom */}
        <div className="flex items-center justify-end gap-1 z-10">
          <ToolButton label="Hear while dragging" icon={scrubAudio ? Headphones : HeadphoneOff}
            onClick={toggleScrubAudio} pressed={scrubAudio} />
          <ToolButton label="Snapping" icon={Magnet} onClick={toggleSnap} pressed={snapEnabled} />
          <ToolButton label="Zoom to fit" icon={Maximize2} onClick={handleZoomToFit} />

          <div className="w-px h-5 bg-white/[0.08] mx-0.5" />

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
                "absolute top-full left-1/2 -translate-x-1/2 mt-2 px-2 py-0.5 rounded bg-ink-900 border border-ink-700 text-[10px] font-mono font-bold text-white shadow-md pointer-events-none transition-all duration-150 z-30 whitespace-nowrap",
                (showZoomTooltip || isDraggingSlider)
                  ? "opacity-100 scale-100 translate-y-0"
                  : "opacity-0 scale-90 -translate-y-1"
              )}
            >
              {Math.round(zoom * 100)}%
              {/* Tooltip arrow (pointing up at the slider) */}
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 -mb-[1px] w-0 h-0 border-l-[4px] border-r-[4px] border-b-[4px] border-l-transparent border-r-transparent border-b-ink-700" />
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 -mb-[2px] w-0 h-0 border-l-[4px] border-r-[4px] border-b-[4px] border-l-transparent border-r-transparent border-b-ink-900" />
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
              className="w-20 md:w-24 appearance-none cursor-pointer bg-ink-800 h-[2px] rounded-lg focus:outline-none opacity-85 hover:opacity-100 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-2.5 [&::-webkit-slider-thumb]:w-2.5 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:shadow [&::-moz-range-thumb]:h-2.5 [&::-moz-range-thumb]:w-2.5 [&::-moz-range-thumb]:bg-white [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0"
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

        </div>
      </div>

      {/* Main Timeline Area (Ruler + Tracks + Splitters) */}
      <div className="flex-1 min-h-0 flex flex-col relative">
        {/* Ruler Row */}
        <div className="flex h-8 bg-ink-950/30 border-b border-white/[0.06] shrink-0 select-none relative">
          {/* Ruler Speaker Header Spacer */}
          <div
            className="h-full border-r border-white/[0.04] shrink-0 bg-ink-900/95 z-20"
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
                  className="absolute top-[7px] left-1/2 -translate-x-1/2 bg-ink-900 border border-red-500/50 px-2 py-0.5 rounded font-mono text-[11px] text-red-400 font-semibold shadow-md whitespace-nowrap select-none z-40 tabular-nums"
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
              className="absolute z-40 border border-brand-400/80 bg-brand-500/10 rounded-sm pointer-events-none"
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
              className="relative border-b border-timeline-grid bg-ink-900/30"
              style={{ height: STEM_HEIGHT }}
            >
              {/* Music & effects header: its icon, mute, volume */}
              <div
                className="sticky left-0 z-10 h-full flex items-center gap-1.5 px-2.5 shrink-0 bg-ink-900/95 backdrop-blur-sm border-r border-white/[0.06]"
                style={{ float: 'left', width: 'var(--speaker-width)' }}
              >
                <Tooltip content="Music & effects" side="right">
                  <Music size={15} className="text-sky-400 shrink-0" />
                </Tooltip>
                <Tooltip content="Music dips under voices" side="bottom">
                  <button
                    onClick={handleToggleDucking}
                    disabled={!jobId || isSettingDucking}
                    aria-label="Music dips under voices"
                    aria-pressed={!!job?.duck_music}
                    className={cn(
                      'h-6 w-6 rounded-md flex items-center justify-center transition-colors shrink-0 disabled:opacity-40',
                      job?.duck_music ? 'bg-sky-500/20 text-sky-300' : 'text-white/45 hover:text-white hover:bg-white/8',
                    )}
                  >
                    <ArrowDownWideNarrow size={13} />
                  </button>
                </Tooltip>
                <Tooltip content={mutedTrackIds['__bgm__'] ? 'Unmute' : 'Mute'} side="bottom">
                  <button
                    onClick={() => toggleMuteTrack('__bgm__')}
                    aria-label={mutedTrackIds['__bgm__'] ? 'Unmute music' : 'Mute music'}
                    aria-pressed={!!mutedTrackIds['__bgm__']}
                    className={cn(
                      'h-6 w-6 rounded-md flex items-center justify-center transition-colors shrink-0',
                      mutedTrackIds['__bgm__'] ? 'bg-amber-500/20 text-amber-400' : 'text-white/45 hover:text-white hover:bg-white/8',
                    )}
                  >
                    {mutedTrackIds['__bgm__'] ? <VolumeX size={13} /> : <Volume2 size={13} />}
                  </button>
                </Tooltip>
                {/* Volume — re-encodes the music stem server-side, so the slider
                    stages locally and only commits (PATCH) on release. */}
                <input
                  type="range"
                  min={-24}
                  max={12}
                  step={1}
                  value={bgmVolume}
                  disabled={!jobId || isSettingBgmVolume}
                  onChange={(e) => setBgmVolumeLocal(Number(e.target.value))}
                  onMouseUp={() => handleBgmVolumeCommit(bgmVolume)}
                  onTouchEnd={() => handleBgmVolumeCommit(bgmVolume)}
                  title="Volume"
                  aria-label="Music volume"
                  className="flex-1 accent-sky-500 bg-ink-800 rounded-lg cursor-pointer h-1 min-w-0 disabled:opacity-40 disabled:cursor-not-allowed"
                />
                <span className="text-[9px] font-mono text-white/40 shrink-0 w-9 text-right tabular-nums">
                  {isSettingBgmVolume ? '…' : `${bgmVolume > 0 ? '+' : ''}${bgmVolume}dB`}
                </span>
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
              className="relative border-b border-timeline-grid bg-ink-900/30"
              style={{ height: STEM_HEIGHT }}
            >
              {/* Original voices header — Analyze lives here (sticky, always visible) */}
              <div
                className="sticky left-0 z-10 h-full flex items-center px-2.5 gap-1.5 shrink-0 bg-ink-900/95 backdrop-blur-sm border-r border-white/[0.06]"
                style={{ float: 'left', width: 'var(--speaker-width)' }}
              >
                <Tooltip content={vocalsReplaced ? 'Original voices — not in the export' : 'Original voices'} side="right">
                  <AudioLines size={15} className={cn(
                    'shrink-0',
                    isStage1Processing ? 'text-emerald-500/40 animate-pulse' : vocalsReplaced ? 'text-ink-500' : 'text-emerald-400',
                  )} />
                </Tooltip>
                <div className="flex items-center gap-1 ml-auto shrink-0">
                  {/* Mute — always there once stems exist, to compare with the dub */}
                  {!isStage1Processing && (
                    <Tooltip content={vocalsMuted ? 'Unmute' : 'Mute'} side="bottom">
                      <button
                        onClick={() => useEditorStore.setState((s) => ({
                          mutedTrackIds: { ...s.mutedTrackIds, __vocals__: !vocalsMuted },
                        }))}
                        aria-label={vocalsMuted ? 'Unmute the original voices' : 'Mute the original voices'}
                        aria-pressed={vocalsMuted}
                        className={cn(
                          'h-6 w-6 rounded-md flex items-center justify-center transition-colors',
                          vocalsMuted ? 'bg-amber-500/20 text-amber-400' : 'text-white/45 hover:text-white hover:bg-white/8',
                        )}
                      >
                        {vocalsMuted ? <VolumeX size={13} /> : <Volume2 size={13} />}
                      </button>
                    </Tooltip>
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
                        className="w-10 px-1 py-0.5 rounded-full bg-ink-800 border border-ink-700 text-ink-200 text-[9px] focus:outline-none focus:border-emerald-500 disabled:opacity-60"
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
                      ? "bg-ink-800/40 border-ink-700/30 opacity-40"
                      : "bg-emerald-500/10 border-emerald-500/25"
                  )}>
                    {vocalsPreviewPath && (
                      <StemWaveform
                        audioUrl={vocalsPreviewPath}
                        color={vocalsReplaced ? "#5A6185" : "#34d399"}
                        pxPerSec={PX_PER_SEC * zoom}
                      />
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Voice tracks — free rows, independent of speaker; only tracks with clips */}
            <div ref={lanesRef} className="relative">
            {rows.map((laneIdx) => {
              const laneSegments = segments.filter((s) => (s.lane_index ?? 0) === laneIdx)

              return (
                <div
                  key={laneIdx}
                  className="relative border-b border-timeline-grid"
                  style={{ height: LANE_HEIGHT }}
                >
                  {/* Track header (sticky left) — no controls on voice tracks */}
                  <div
                    className="sticky left-0 z-10 h-full shrink-0 bg-ink-900/95 backdrop-blur-sm border-r border-white/[0.06]"
                    style={{ float: 'left', width: 'var(--speaker-width)' }}
                  />

                  {/* Segments */}
                  <div
                    className="absolute top-0 bottom-0 right-0"
                    style={{ left: 'var(--speaker-width)' }}
                  >
                    {/* Where the actor spoke: a bracket that stays put when the clip moves */}
                    {laneSegments.map((seg) => seg.source_start != null && seg.source_end != null && (
                      <div
                        key={`span-${seg.id}`}
                        className="absolute bottom-[2px] h-[4px] border-x-2 border-b-2 border-ink-500/80 rounded-b-[2px] pointer-events-none"
                        style={{
                          left: timeToPixels(seg.source_start, zoom, PX_PER_SEC),
                          width: Math.max(2, timeToPixels(seg.source_end - seg.source_start, zoom, PX_PER_SEC)),
                        }}
                      />
                    ))}
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
                          rows={rows}
                          allSegments={segments}
                          PX_PER_SEC={PX_PER_SEC}
                          lanesRef={lanesRef}
                          insertLineRef={insertLineRef}
                          overruns={overruns[seg.id]}
                          onCommitLayout={handleCommitLayout}
                          onContextMenu={handleClipContextMenu}
                          onPatch={handleClipPatch}
                          onSelect={handleSegmentSelect}
                          onDelete={handleSegmentDelete}
                          onRegenerate={jobId ? handleSegmentRegenerate : undefined}
                          isPersisting={isLayingOut && !!layoutVariables?.changes.some((c) => c.id === seg.id)}
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
              {/* Where a drop would insert a new track */}
              <div
                ref={insertLineRef}
                className="absolute right-0 h-[2px] bg-brand-500 pointer-events-none z-40"
                style={{ left: 'var(--speaker-width)', display: 'none', boxShadow: '0 0 6px rgba(240,80,110,0.8)' }}
              />
            </div>

            {/* Empty state — not while a step runs: its progress shows in the
                transcript panel, and only there */}
            {segments.length === 0 && !(job && isJobRunning(job.status)) && (
              <div className="flex flex-col items-center justify-center h-24 text-text-disabled text-xs gap-3 w-full">
                <span>No lines yet</span>
              </div>
            )}
          </div>
        </div>

        {clipMenu && (
          <ContextMenu x={clipMenu.x} y={clipMenu.y} items={clipMenuItems(clipMenu)} onClose={closeClipMenu} />
        )}

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
    // pxPerSec is only the initial zoom: recreating the instance on every zoom
    // step would re-download and re-decode the stem — the effect below zooms it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
