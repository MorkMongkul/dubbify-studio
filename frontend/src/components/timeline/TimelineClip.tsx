// src/components/timeline/TimelineClip.tsx
// One clip on the timeline (0.3.0): a clip with a voice is exactly as long as
// the sound it plays — its edges cut sound, never change its speed — and a clip
// with no voice yet is as long as its text takes to say (no edges to drag).
// Dragging moves it in time and between tracks like CapCut; a drop never moves
// another clip in time (lib/tracks.ts planDrop).
import { memo, useRef, useEffect, useState, useMemo } from 'react'
import { Loader2, Gauge, FilePenLine, Lock } from 'lucide-react'
import type { Segment, SegmentUpdate, LayoutChange } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { timeToPixels, pixelsToTime, hexToRgba, cn } from '@/lib/utils'
import {
  clampFades, effectiveSpeed, isVoiceOutdated, isVoiced, selectionTargets, shiftedVolume, trimEnd, trimStart, voiceUrl,
  waveformSlice,
} from '@/lib/clipRules'
import { dbLabel, gainFor, levelBars, peaksFrom, volumeAfterDrag, volumeTop } from '@/lib/waveform'
import { dropSpot, planDrop, type DropSpot } from '@/lib/tracks'
import { previewClip } from '@/hooks/usePreviewAudio'
import { DragTip } from './DragTip'

export const LANE_HEIGHT = 64  // clip row height — also the divisor for drop-target row math
// The waveform band at the bottom of a clip (px) — the volume line moves inside it
const WAVE_BAND_PX = 21
// Bars past the export's loudness limit (lib/waveform.ts CEILING)
const LOUD_COLOR = '#F87171'

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
  /** The tracks drawn, top to bottom (lane numbers; empty tracks aren't drawn). */
  rows: number[]
  allSegments: Segment[]
  PX_PER_SEC: number
  lanesRef: React.RefObject<HTMLDivElement | null>
  /** The coral line that shows where a drop would insert a new track. */
  insertLineRef: React.RefObject<HTMLDivElement | null>
  /** The parts of this clip's voice that run into a later line (red stripes). */
  overruns?: [number, number][]
  /** One layout request + one undo step for the gesture (TimelineEditor.handleCommitLayout). */
  /** `moving`: the clips a drag moved (a drop that inserts a track moves the switches below it). */
  onCommitLayout: (changes: LayoutChange[], label: string, moving?: string[]) => void
  /** Its track's switches: locked = no edits; hidden = its subtitle isn't shown; muted = not heard. */
  locked?: boolean
  hidden?: boolean
  muted?: boolean
  /** Every locked track: nothing is dropped onto them, and a group drag leaves their clips. */
  lockedLanes?: Set<number>
  /** A clip setting changed on the clip itself (fades, the volume line): one PATCH + one undo step. */
  onPatch?: (seg: Segment, after: SegmentUpdate, before: SegmentUpdate, label: string) => void
  /** A volume line was dragged: each clip's new dB (one undo step); clears the live volume once saved. */
  onVolumeChange?: (targets: Record<string, number>) => void
  onSelect: (seg: Segment) => void
  onContextMenu?: (seg: Segment, x: number, y: number) => void
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
export const InteractiveSegment = memo(function InteractiveSegment({
  seg, color, isActive, isSelected, onToggleSelect, isApproved, zoom, duration, rows, allSegments, PX_PER_SEC,
  lanesRef, insertLineRef, overruns, onCommitLayout, onPatch, onVolumeChange, onSelect, onContextMenu,
  isPersisting, isRegenerating, isSplitting,
  mutedTrackIds, soloedTrackIds, volume,
  activeTool, snapEnabled, onSplit,
  locked = false, hidden = false, muted = false, lockedLanes,
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
  const voiced = isVoiced(seg)
  const outdated = isVoiceOutdated(seg)
  const speed = effectiveSpeed(seg)
  const showSpeed = voiced && Math.abs(speed - 1) > 0.005
  const text = seg.khmer_text || seg.source_text

  // Cache-busted URL — the backend overwrites the same file path on every
  // re-synthesis, so a version query param (tts_duration_secs changes on
  // every real synth) is what actually busts the browser + decodedPeaksCache.
  // Uses tts_audio_url (the /uploads/... URL), not tts_audio_path (an absolute
  // filesystem path that isn't fetchable from the browser).
  const versionedAudioPath = seg.tts_audio_url ? voiceUrl(seg) : undefined
  const slice = useMemo(() => waveformSlice(seg), [seg])

  // Fades and the volume line, while one is being dragged (saved on release)
  const [fadeDraft, setFadeDraft] = useState<{ fade_in_secs: number; fade_out_secs: number } | null>(null)
  // A volume line being dragged — this clip's, or another selected clip's —
  // moves this clip's line by the same amount (editorStore.liveClipVolume)
  const liveTarget = useEditorStore((s) => s.liveClipVolume?.[seg.id] ?? null)
  // While this clip's own line is dragged: the pointer (its dB tip follows it) and the tip's text
  const [volumeTip, setVolumeTip] = useState<{ x: number; y: number; text: string } | null>(null)
  const fadeIn = fadeDraft?.fade_in_secs ?? seg.fade_in_secs ?? 0
  const fadeOut = fadeDraft?.fade_out_secs ?? seg.fade_out_secs ?? 0
  const fadeInPx = timeToPixels(fadeIn, zoom, PX_PER_SEC)
  const fadeOutPx = timeToPixels(fadeOut, zoom, PX_PER_SEC)
  const shownVolume = liveTarget ?? seg.volume_db ?? 0

  // Drag a top corner inward: a fade, as long as the drag (never past the other fade)
  const startFadeDrag = (e: React.PointerEvent<HTMLDivElement>, side: 'in' | 'out') => {
    e.stopPropagation()
    e.preventDefault()
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const from = side === 'in' ? seg.fade_in_secs ?? 0 : seg.fade_out_secs ?? 0
    let draft = { fade_in_secs: seg.fade_in_secs ?? 0, fade_out_secs: seg.fade_out_secs ?? 0 }
    const move = (ev: PointerEvent) => {
      const dx = pixelsToTime(ev.clientX - startX, zoom, PX_PER_SEC)
      const wanted = from + (side === 'in' ? dx : -dx)
      draft = side === 'in'
        ? clampFades(seg, wanted, seg.fade_out_secs ?? 0)
        : clampFades(seg, seg.fade_in_secs ?? 0, wanted)
      setFadeDraft(draft)
    }
    const up = (ev: PointerEvent) => {
      el.releasePointerCapture(ev.pointerId)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setFadeDraft(null)
      const field = side === 'in' ? 'fade_in_secs' : 'fade_out_secs'
      if (Math.abs(draft[field] - from) > 0.0005) {
        onPatch?.(seg, { [field]: draft[field] }, { [field]: from }, side === 'in' ? 'Fade in' : 'Fade out')
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  // Drag the volume line up or down: 2 px per dB from where it was grabbed (so it
  // never jumps), after a small dead zone; the waveform, the preview and the
  // dB tip follow at once, saved on release. A selected clip carries the whole
  // selection's voiced clips with it, each by the same dB.
  const startVolumeDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)
    const y0 = e.clientY
    const from = seg.volume_db ?? 0
    const voices = selectionTargets(useEditorStore.getState().selectedSegmentIds, seg.id)
      .map((id) => (id === seg.id ? seg : allSegments.find((s) => s.id === id)))
      .filter((s): s is Segment => !!s && (s.id === seg.id || isVoiced(s)))
    let targets: Record<string, number> | null = null
    const move = (ev: PointerEvent) => {
      const delta = volumeAfterDrag(from, ev.clientY - y0) - from
      targets = Object.fromEntries(voices.map((s) => [s.id, shiftedVolume(s.volume_db ?? 0, delta)]))
      useEditorStore.getState().setLiveClipVolume(targets)
      setVolumeTip({
        x: ev.clientX, y: ev.clientY,
        text: voices.length > 1 ? `${dbLabel(delta)} on ${voices.length} clips` : dbLabel(from + delta),
      })
    }
    const end = (ev: PointerEvent, save: boolean) => {
      if (el.hasPointerCapture(ev.pointerId)) el.releasePointerCapture(ev.pointerId)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel)
      setVolumeTip(null)
      const changed = targets && voices.some((s) => targets![s.id] !== (s.volume_db ?? 0))
      if (save && changed && onVolumeChange) onVolumeChange(targets!)
      else if (useEditorStore.getState().liveClipVolume === targets) useEditorStore.getState().setLiveClipVolume(null)
    }
    const up = (ev: PointerEvent) => end(ev, true)
    const cancel = (ev: PointerEvent) => end(ev, false)
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel)
  }

  // Double-click the volume line: back to 0 dB
  const resetVolume = (e: React.MouseEvent) => {
    e.stopPropagation()
    if ((seg.volume_db ?? 0) !== 0) onVolumeChange?.({ [seg.id]: 0 })
  }

  // Double-click: hear this clip's window alone — what the export plays for it
  const playAudio = () => {
    const key = seg.speaker_id ?? '__none__'
    const hasSolo = Object.values(soloedTrackIds).some(Boolean)
    previewClip(seg, mutedTrackIds[key] || (hasSolo && !soloedTrackIds[key]) ? 0 : volume)
  }

  const handlePointerDown = (
    e: React.PointerEvent<HTMLDivElement>,
    actionType: 'drag' | 'resize-left' | 'resize-right'
  ) => {
    e.stopPropagation()
    const element = elementRef.current
    if (!element) return

    element.setPointerCapture(e.pointerId)

    const startX = e.clientX
    const startLeft = timeToPixels(seg.start_time, zoom, PX_PER_SEC)
    const startWidth = timeToPixels(seg.end_time - seg.start_time, zoom, PX_PER_SEC)
    const maxTimelineWidth = timeToPixels(duration || 60, zoom, PX_PER_SEC)
    const myRow = rows.indexOf(seg.lane_index ?? 0)

    // Group drag: dragging a clip that's part of the multi-selection moves
    // EVERY selected clip by the same time and track delta.
    const selectedIds = useEditorStore.getState().selectedSegmentIds
    const isGroupDrag = actionType === 'drag' && selectedIds.length > 1 && selectedIds.includes(seg.id)
    // A locked track's clips stay where they are
    const lockedIds = new Set(lockedLanes ? allSegments.filter((s) => lockedLanes.has(s.lane_index ?? 0)).map((s) => s.id) : [])
    const groupIdSet = new Set((isGroupDrag ? selectedIds : [seg.id]).filter((id) => !lockedIds.has(id)))
    const groupMates = isGroupDrag ? allSegments.filter((s) => s.id !== seg.id && groupIdSet.has(s.id)) : []
    const mateEls = groupMates
      .map((s) => document.querySelector<HTMLElement>(`[data-seg-id="${s.id}"]`))
      .filter((el): el is HTMLElement => !!el)
    // Nothing in the group goes before 0
    const groupStartPx = Math.min(startLeft, ...groupMates.map((s) => timeToPixels(s.start_time, zoom, PX_PER_SEC)))

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

    let lastLeft = startLeft
    let lastSpot: DropSpot = { kind: 'onto', row: myRow }
    let trimmed: LayoutChange | null = null
    let moved = false

    const spotAt = (clientY: number): DropSpot => {
      const lanesEl = lanesRef.current
      if (!lanesEl) return { kind: 'onto', row: myRow }
      return dropSpot(clientY - lanesEl.getBoundingClientRect().top, rows.length, LANE_HEIGHT)
    }
    const showInsertLine = (spot: DropSpot) => {
      const line = insertLineRef.current
      if (!line) return
      if (spot.kind === 'insert') {
        line.style.display = 'block'
        line.style.top = `${spot.boundary * LANE_HEIGHT - 1}px`
      } else {
        line.style.display = 'none'
      }
    }

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const deltaX = moveEvent.clientX - startX
      if (Math.abs(deltaX) > 2 || Math.abs(moveEvent.clientY - e.clientY) > 2) moved = true
      let snapped = false

      if (actionType === 'drag') {
        let newLeft = startLeft + deltaX
        const snappedLeft = snapValue(newLeft)
        const snappedRight = snapValue(newLeft + startWidth)
        if (snappedLeft !== newLeft) { newLeft = snappedLeft; snapped = true }
        else if (snappedRight !== newLeft + startWidth) { newLeft = snappedRight - startWidth; snapped = true }
        const delta = Math.max(-groupStartPx, Math.min(maxTimelineWidth - startLeft, newLeft - startLeft))
        lastLeft = startLeft + delta
        // Tracks: the clip follows the rows; near a row's edge a new track would be inserted
        lastSpot = spotAt(moveEvent.clientY)
        showInsertLine(lastSpot)
        const rowDelta = lastSpot.kind === 'onto' ? lastSpot.row - myRow : 0
        const dy = lastSpot.kind === 'onto' ? rowDelta * LANE_HEIGHT : (lastSpot.boundary - myRow - 0.5) * LANE_HEIGHT
        // Compositor-only transform — no layout reflow per frame
        element.style.transform = `translate(${delta}px, ${dy}px)`
        for (const el of mateEls) {
          el.style.transform = `translate(${delta}px, ${dy}px)`
          el.style.opacity = '0.9'
          el.style.zIndex = '49'
        }
      } else if (actionType === 'resize-left') {
        let newLeftPx = startLeft + deltaX
        const snappedLeft = snapValue(newLeftPx)
        if (snappedLeft !== newLeftPx) { newLeftPx = snappedLeft; snapped = true }
        // Cuts sound from the start: never past the voice's own start, never the speed
        const r = trimStart(seg, pixelsToTime(newLeftPx, zoom, PX_PER_SEC))
        const l = timeToPixels(r.start_time, zoom, PX_PER_SEC)
        element.style.left = `${l}px`
        element.style.width = `${Math.max(4, startLeft + startWidth - l)}px`
        trimmed = { id: seg.id, start_time: r.start_time, voice_offset: r.voice_offset }
      } else {
        let newRightPx = startLeft + startWidth + deltaX
        const snappedRight = snapValue(newRightPx)
        if (snappedRight !== newRightPx) { newRightPx = snappedRight; snapped = true }
        const r = trimEnd(seg, pixelsToTime(newRightPx, zoom, PX_PER_SEC))
        element.style.width = `${Math.max(4, timeToPixels(r.end_time - seg.start_time, zoom, PX_PER_SEC))}px`
        trimmed = { id: seg.id, end_time: r.end_time }
      }

      // Cheap, non-reactive visual cue when snapped — direct style mutation,
      // no React state, consistent with the rest of this drag implementation.
      element.style.boxShadow = snapped ? '0 0 0 2px rgba(244,114,141,0.9)' : ''
    }

    const handlePointerUp = (upEvent: PointerEvent) => {
      element.releasePointerCapture(upEvent.pointerId)
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)

      element.style.zIndex = ''
      element.style.opacity = ''
      element.style.boxShadow = ''
      element.style.transform = ''
      for (const el of mateEls) {
        el.style.transform = ''
        el.style.opacity = ''
        el.style.zIndex = ''
      }
      if (insertLineRef.current) insertLineRef.current.style.display = 'none'
      // A trim stays drawn where it ended; the in-flight overlay (then the
      // server's answer) takes over from there. Nothing sent: put it back.
      const restore = () => {
        element.style.left = `${startLeft}px`
        element.style.width = `${startWidth}px`
      }

      if (!moved) { if (actionType !== 'drag') restore(); return }   // a click: nothing to send
      if (actionType === 'drag') {
        const deltaSecs = Math.round(pixelsToTime(lastLeft - startLeft, zoom, PX_PER_SEC) * 1000) / 1000
        const ids = [seg.id, ...groupMates.map((s) => s.id)]
        const changes = planDrop(allSegments, ids, seg.id, deltaSecs, lastSpot, lockedLanes)
        onCommitLayout(changes, ids.length > 1 ? `Move ${ids.length} clips` : 'Move clip', ids)
      } else if (trimmed) {
        const same = actionType === 'resize-left'
          ? Math.abs((trimmed.start_time ?? seg.start_time) - seg.start_time) < 1e-6
          : Math.abs((trimmed.end_time ?? seg.end_time) - seg.end_time) < 1e-6
        if (same) restore()
        else onCommitLayout([trimmed], actionType === 'resize-left' ? 'Trim start' : 'Trim end')
      } else {
        restore()
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
        'timeline-segment absolute top-[6px] bottom-[6px] select-none overflow-hidden transition-[box-shadow,outline]',
        activeTool === 'blade' ? 'cursor-crosshair' : locked ? 'cursor-default' : 'cursor-grab active:cursor-grabbing',
        isActive && 'active border-brand-500 outline-2 outline-brand-500 ring-2 ring-brand-500/20',
        isSplitting && 'opacity-60',
        // A hidden or muted track's clips are drawn faded
        (hidden || muted) && !isSplitting && 'opacity-50',
      )}
      style={{
        left,
        width,
        background: isApproved
          ? `linear-gradient(180deg, ${hexToRgba(color, 0.8)} 0%, ${hexToRgba(color, 0.95)} 100%)`
          : `linear-gradient(180deg, ${hexToRgba(color, 0.35)} 0%, ${hexToRgba(color, 0.45)} 100%)`,
        // A clip with no voice yet: dashed — its length is its text's, until a voice sets it
        borderStyle: voiced ? 'solid' : 'dashed',
        borderWidth: voiced ? '1px 1px 1px 3px' : '1px',
        borderColor: voiced
          ? `${hexToRgba(color, isApproved ? 0.8 : 0.4)} ${hexToRgba(color, isApproved ? 0.6 : 0.3)} ${hexToRgba(color, isApproved ? 0.6 : 0.3)} ${color}`
          : hexToRgba(color, 0.75),
        borderRadius: '8px',
        boxShadow: isActive
          ? `0 0 12px ${hexToRgba('#F0506E', 0.6)}`
          : isSelected ? '0 0 8px rgba(244,114,141,0.35)' : 'none',
        outline: isActive
          ? '2px solid #F0506E'
          : isSelected ? '2px solid rgba(244,114,141,0.85)' : 'none',
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
        // A locked track's clip can be picked, never moved
        if (!locked) handlePointerDown(e, 'drag')
        // Grabbing a clip that's already part of the multi-selection must NOT
        // collapse the selection (onSelect clears it) — that press is the
        // start of a group drag. Plain click on an unselected clip keeps the
        // standard replace-selection behaviour.
        if (!isSelected) {
          onSelect(seg)
        } else {
          // Still focus this clip and open its settings, selection intact —
          // volume/filter/speed edits there apply to the whole selection.
          const st = useEditorStore.getState()
          st.setActiveSegment(seg.id)
          st.setInspectorMode('audio_clip_settings')
        }
      }}
      onMouseDown={(e) => {
        e.stopPropagation()
      }}
      onDoubleClick={(e) => {
        e.stopPropagation()
        playAudio()
      }}
      onContextMenu={(e) => {
        if (!onContextMenu) return
        e.preventDefault()
        e.stopPropagation()
        onContextMenu(seg, e.clientX, e.clientY)
      }}
      // The whole line, whatever the zoom: a short clip can be too narrow to read
      title={text}
    >
      {/* Where the voice runs into a later line (any track) — the original didn't overlap there */}
      {overruns?.map(([from, to]) => (
        <div
          key={from}
          className="absolute top-0 bottom-0 pointer-events-none"
          style={{
            left: timeToPixels(from - seg.start_time, zoom, PX_PER_SEC),
            width: Math.max(2, timeToPixels(to - from, zoom, PX_PER_SEC)),
            background: 'repeating-linear-gradient(135deg, rgba(239,68,68,0.55) 0 4px, rgba(239,68,68,0.12) 4px 8px)',
          }}
        />
      ))}

      {/* Left Trim Handle — a voiced clip only (a voiceless one follows its text);
          hidden while the blade tool is active so a click cuts instead */}
      {activeTool === 'select' && voiced && !locked && (
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

      {/* Text band — across the top of a voiced clip (the waveform sits below
          it, never through the Khmer); the whole clip, two lines, without a voice */}
      {width > 28 && (
        <div
          className={cn(
            'absolute left-0 right-0 top-0 flex gap-1.5 pl-3 pointer-events-none select-none',
            voiced
              ? 'h-[27px] items-center bg-gradient-to-b from-black/45 via-black/25 to-transparent'
              : 'bottom-0 items-start pt-1 bg-black/20',
            isApproved ? 'pr-7' : 'pr-2',
          )}
        >
          <span
            className={cn('h-2 w-2 rounded-full shrink-0 ring-1 ring-black/40', !voiced && 'mt-[7px]')}
            style={{ background: color }}
          />
          {showSpeed && (
            <span className="shrink-0 flex items-center gap-0.5 rounded bg-black/45 px-1 text-[10.5px] font-semibold text-white tabular-nums">
              <Gauge size={11} />{`${Math.round(speed * 100) / 100}×`}
            </span>
          )}
          {locked && (
            <span className="shrink-0 text-amber-300" title="Track locked">
              <Lock size={11} />
            </span>
          )}
          {outdated && (
            <span className="shrink-0 text-amber-300" title="Voice made from older text">
              <FilePenLine size={12} />
            </span>
          )}
          <span
            className={cn(
              'text-[13px] font-semibold text-white leading-[1.7] min-w-0',
              voiced ? 'truncate' : 'line-clamp-2 whitespace-normal break-words leading-[1.45]',
            )}
            style={{ textShadow: '0 1px 3px rgba(0,0,0,0.9)' }}
          >
            {text}
          </span>
        </div>
      )}

      {/* The voice's waveform at true scale: only the part the clip plays.
          Mounted only once the clip has scrolled into view. */}
      {voiced && versionedAudioPath && waveVisible && (
        <SegmentWaveform
          audioPath={versionedAudioPath}
          width={width}
          slice={slice}
          gain={gainFor(shownVolume - (seg.volume_db ?? 0))}
        />
      )}

      {/* Fades: a shaded slope at each end */}
      {voiced && (fadeIn > 0 || fadeOut > 0) && (
        <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox={`0 0 ${width} 100`} preserveAspectRatio="none">
          {fadeIn > 0 && <polygon points={`0,0 ${fadeInPx},0 0,100`} fill="rgba(0,0,0,0.35)" />}
          {fadeOut > 0 && <polygon points={`${width},0 ${width - fadeOutPx},0 ${width},100`} fill="rgba(0,0,0,0.35)" />}
          {fadeIn > 0 && <line x1={0} y1={100} x2={fadeInPx} y2={0} stroke="rgba(255,255,255,0.6)" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
          {fadeOut > 0 && <line x1={width} y1={100} x2={width - fadeOutPx} y2={0} stroke="rgba(255,255,255,0.6)" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
        </svg>
      )}

      {/* Fade handles on the top corners of the selected clip */}
      {isActive && voiced && activeTool === 'select' && onPatch && !locked && width > 36 && (
        <>
          <div
            className="absolute top-0 z-30 h-3 w-3 -translate-x-1/2 rounded-full border border-white/80 bg-ink-900 cursor-ew-resize hover:bg-brand-500"
            style={{ left: Math.max(6, fadeInPx) }}
            title="Fade in"
            onPointerDown={(e) => startFadeDrag(e, 'in')}
          />
          <div
            className="absolute top-0 z-30 h-3 w-3 translate-x-1/2 rounded-full border border-white/80 bg-ink-900 cursor-ew-resize hover:bg-brand-500"
            style={{ right: Math.max(6, fadeOutPx) }}
            title="Fade out"
            onPointerDown={(e) => startFadeDrag(e, 'out')}
          />
        </>
      )}

      {/* The volume line: it follows the pointer, and the waveform grows and
          shrinks with it; its dB shows on hover and while dragging */}
      {voiced && onVolumeChange && !locked && width >= 40 && activeTool === 'select' && (
        <div className="absolute inset-x-2.5 bottom-[3px] z-20 pointer-events-none" style={{ height: WAVE_BAND_PX }}>
          <div
            className="absolute inset-x-0 h-[9px] -translate-y-1/2 cursor-ns-resize pointer-events-auto group/vol"
            style={{ top: volumeTop(shownVolume, WAVE_BAND_PX) }}
            aria-label="Volume"
            onPointerDown={startVolumeDrag}
            onDoubleClick={resetVolume}
          >
            <div className={cn(
              'absolute inset-x-0 top-1/2 h-px',
              liveTarget != null ? 'bg-brand-300' : 'bg-white/45 group-hover/vol:bg-white/90',
            )} />
            <span className={cn(
              'absolute right-0 top-1/2 -translate-y-1/2 rounded bg-ink-900/90 px-1.5 text-[10.5px] font-semibold text-white tabular-nums pointer-events-none',
              liveTarget != null ? 'hidden' : 'hidden group-hover/vol:block',
            )}>
              {dbLabel(shownVolume)}
            </span>
          </div>
          {volumeTip && <DragTip x={volumeTip.x} y={volumeTip.y} text={volumeTip.text} />}
        </div>
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

      {/* Splitting — the request is in flight */}
      {isSplitting && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/25 pointer-events-none">
          <Loader2 size={14} className="animate-spin text-white/90" />
        </div>
      )}

      {/* Persisting dot — reflects the real in-flight layout request */}
      {isPersisting && (
        <div className="absolute top-1 left-1 h-1.5 w-1.5 rounded-full bg-white/70 animate-pulse pointer-events-none" title="Saving…" />
      )}

      {/* Approved Indicator */}
      {isApproved && (
        <div
          className="absolute top-2 right-3.5 h-2 w-2 rounded-full bg-emerald-400 border border-black/30 shadow-sm pointer-events-none"
          title="Approved"
        />
      )}

      {/* Right Trim Handle — see note on the left handle */}
      {activeTool === 'select' && voiced && !locked && (
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

// Absolute peaks (the loudest channel), about `numPeaks` per file — not scaled to
// the clip's own loudest point, so the waveform shows the voice's real level and
// can follow its volume line (lib/waveform.ts).
function extractPeaks(buffer: AudioBuffer, numPeaks = 60): number[] {
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c))
  return peaksFrom(channels, buffer.length, numPeaks)   // the whole file as one "second"
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
  /** The part of the file to draw, as fractions of it (clipRules.waveformSlice). */
  slice?: { from: number; to: number }
  color?: string   // hex or CSS color — defaults to white (matches segment block)
  loading?: boolean // external loading hint (e.g. stem is being generated)
  /** Amplitude factor while the volume line is dragged (the file already carries the saved volume). */
  gain?: number
}

function SegmentWaveform({ audioPath, width, slice, color = 'white', loading: externalLoading, gain = 1 }: SegmentWaveformProps) {
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
  // Only the part of the voice the clip plays, drawn across the clip — at true scale.
  const numPeaks = Math.max(20, Math.floor(width / 3.5))
  const from = slice?.from ?? 0
  const to = slice?.to ?? 1
  const peaks = useMemo(() => {
    if (!highResPeaks) return null
    const a = Math.floor(from * highResPeaks.length)
    const b = Math.max(a + 1, Math.ceil(to * highResPeaks.length))
    return downsamplePeaks(highResPeaks.slice(a, b), numPeaks)
  }, [highResPeaks, numPeaks, from, to])
  const bars = useMemo(() => (peaks ? levelBars(peaks, gain) : null), [peaks, gain])

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
  if (error || !peaks || !bars) {
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
      {bars.map((bar, i) => (
        // At the voice's real level; red where it passes the export's limit
        <rect
          key={i}
          x={i}
          y={(1 - bar.h) / 2}
          width={0.72}
          height={bar.h}
          fill={bar.loud ? LOUD_COLOR : color}
          rx={0.18}
        />
      ))}
    </svg>
  )
}

