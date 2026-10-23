// src/components/timeline/TimelineClip.tsx
// One clip on the timeline: drag, trim, blade, select — and its voice's waveform.
import { memo, useRef, useEffect, useState, useMemo } from 'react'
import { AlertTriangle, Loader2, X, RefreshCw } from 'lucide-react'
import type { Segment, LayoutChange } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { timeToPixels, pixelsToTime, hexToRgba, cn } from '@/lib/utils'
import { previewClip } from '@/hooks/useVoicePlayback'

export const LANE_HEIGHT = 64  // clip row height — also the divisor for drop-target lane math

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
  /** One layout request + one undo step for the gesture (TimelineEditor.handleCommitLayout). */
  onCommitLayout: (changes: LayoutChange[], label: string) => void
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
export const InteractiveSegment = memo(function InteractiveSegment({
  seg, color, isActive, isSelected, onToggleSelect, isApproved, zoom, duration, laneIndices, allSegments, PX_PER_SEC, scrollRef, lanesRef,
  onCommitLayout, onSelect, onDelete, onRegenerate,
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
    const container = scrollRef.current
    if (!element || !container) return

    element.setPointerCapture(e.pointerId)

    const startX = e.clientX
    const startY = e.clientY
    const startLeft = parseFloat(element.style.left) || timeToPixels(seg.start_time, zoom, PX_PER_SEC)
    const startWidth = parseFloat(element.style.width) || timeToPixels(seg.end_time - seg.start_time, zoom, PX_PER_SEC)

    const beforeGesture = { start_time: seg.start_time, end_time: seg.end_time, lane_index: seg.lane_index ?? 0 }

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
      element.style.boxShadow = snapped ? '0 0 0 2px rgba(244,114,141,0.9)' : ''
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

      const changed = newStartTime !== beforeGesture.start_time
        || newEndTime !== beforeGesture.end_time
        || finalLaneIndex !== beforeGesture.lane_index
      if (!changed) return
      const changes: LayoutChange[] = [{ id: seg.id, start_time: newStartTime, end_time: newEndTime, lane_index: finalLaneIndex }]
      if (isGroupDrag) {
        // Every other selected clip moves by the delta the grabbed clip committed to
        const deltaSecs = newStartTime - beforeGesture.start_time
        for (const mate of groupMates) {
          const mateDuration = mate.end_time - mate.start_time
          const mateLane = mate.lane_index ?? 0
          const mateNeighbors = allSegments.filter(
            (s) => !groupIdSet.has(s.id) && (s.lane_index ?? 0) === mateLane
          )
          let mateStart = Math.max(0, mate.start_time + deltaSecs)
          mateStart = resolveDragOverlap(mateStart, mateDuration, mateNeighbors)
          mateStart = Math.round(mateStart * 1000) / 1000
          changes.push({ id: mate.id, start_time: mateStart, end_time: Math.round((mateStart + mateDuration) * 1000) / 1000, lane_index: mateLane })
        }
      }
      onCommitLayout(changes, isGroupDrag ? `Move ${changes.length} clips` : actionType === 'drag' ? 'Move clip' : 'Trim clip')
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
        isActive && 'active border-brand-500 outline-2 outline-brand-500 ring-2 ring-brand-500/20',
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
        handlePointerDown(e, 'drag')
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
              className="h-4 w-4 rounded-full bg-ink-700 hover:bg-ink-600 flex items-center justify-center shadow transition-colors disabled:opacity-50"
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
            title="Delete line (Delete key)"
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

