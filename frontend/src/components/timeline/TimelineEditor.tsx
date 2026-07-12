// src/components/timeline/TimelineEditor.tsx
import { useRef, useCallback, useEffect, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import { ZoomIn, ZoomOut, AlertTriangle, Loader2, Scissors, X, RefreshCw } from 'lucide-react'
import type { Segment, Speaker, Job } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { useUpdateSegment, useDeleteSegment, useSynthesizeSegment } from '@/hooks/useApi'
import { timeToPixels, pixelsToTime, formatTime, getSpeakerColor, hexToRgba, cn, isJobRunning, getJobStatusConfig } from '@/lib/utils'
import { Tooltip } from '@/components/ui/Tooltip'

const PX_PER_SEC = 100 // base pixels per second at zoom=1

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
  const [isDragging, setIsDragging] = useState(false)
  const [showZoomTooltip, setShowZoomTooltip] = useState(false)
  const [isDraggingSlider, setIsDraggingSlider] = useState(false)

  const {
    currentTime, zoom, activeSegmentId, volume,
    mutedTrackIds, soloedTrackIds,
    timelineHeight, speakerPanelWidth,
    setCurrentTime, setActiveSegment, zoomIn, zoomOut,
    updateSegmentPosition, toggleMuteTrack, toggleSoloTrack,
    setSpeakerPanelWidth, setZoom,
    setInspectorMode, setFocusedTimelineItemId
  } = useEditorStore()

  const { mutate: updateSegment, isPending: isUpdatingSegment, variables: updateVariables } = useUpdateSegment()
  const { mutate: deleteSegment } = useDeleteSegment()
  const { mutate: regenerateSegment, isPending: isRegenerating, variables: regenVariables } = useSynthesizeSegment()

  // Delete / Backspace key removes the active segment (skip when typing in an input)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement).isContentEditable) return
      if (!activeSegmentId || !jobId) return
      e.preventDefault()
      deleteSegment({ segmentId: activeSegmentId, jobId })
      setActiveSegment(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeSegmentId, jobId, deleteSegment, setActiveSegment])

  const totalWidth = Math.max(timeToPixels(duration || 60, zoom, PX_PER_SEC), 800)

  // Stem waveform paths — use tiny 8kHz mono preview files so Web Audio decodes instantly.
  // Full stems (~19MB) are kept for playback; preview files (~1.7MB) are for visualization only.
  const noVocalsPreviewPath = jobId && projectId ? `uploads/${projectId}/${jobId}/no_vocals.preview.wav` : null
  const vocalsPreviewPath   = jobId && projectId ? `uploads/${projectId}/${jobId}/vocals.preview.wav`    : null

  // Group segments by speaker — track order must be stable and independent of
  // segment start_time, otherwise dragging a segment can shift which speaker's
  // segment comes first in time and reshuffle every track vertically. Order by
  // the speaker's own (stable) label instead; segments with no speaker sort last.
  const speakerIdsPresent = new Set(segments.map((s) => s.speaker_id ?? '__none__'))
  const speakerIds = [
    ...speakers
      .filter((sp) => speakerIdsPresent.has(sp.id))
      .sort((a, b) => a.label.localeCompare(b.label))
      .map((sp) => sp.id),
    ...(speakerIdsPresent.has('__none__') ? ['__none__'] : []),
  ]

  // Compute speaker color map
  const speakerColorMap = new Map<string, string>()
  speakers.forEach((sp, i) => {
    speakerColorMap.set(sp.id, sp.color ?? getSpeakerColor(i))
  })

  // Auto-scroll playhead into view
  useEffect(() => {
    if (isDragging) return
    const container = scrollRef.current
    if (!container) return
    const playheadX = timeToPixels(currentTime, zoom, PX_PER_SEC)
    const { scrollLeft, clientWidth } = container
    const margin = clientWidth * 0.3
    if (playheadX < scrollLeft + margin || playheadX > scrollLeft + clientWidth - margin) {
      container.scrollLeft = playheadX - clientWidth * 0.4
    }
  }, [currentTime, zoom, isDragging])

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
    setIsDragging(true)
    seekFromEvent(e.clientX, e.currentTarget.getBoundingClientRect())
    const onMove  = (ev: MouseEvent) => seekFromEvent(ev.clientX, (e.currentTarget as HTMLElement).getBoundingClientRect())
    const onUp    = () => { setIsDragging(false); window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
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

  const onTracksMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return
    const scrollEl = scrollRef.current
    if (!scrollEl) return
    const rect = scrollEl.getBoundingClientRect()
    const relativeX = e.clientX - rect.left
    if (relativeX < speakerPanelWidth) return // ignore clicks on track headers

    setIsDragging(true)
    seekFromTrackEvent(e.clientX)

    const onMove = (ev: MouseEvent) => seekFromTrackEvent(ev.clientX)
    const onUp = () => {
      setIsDragging(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [seekFromTrackEvent, speakerPanelWidth])

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


  // Time ruler ticks
  const renderRulerTicks = () => {
    const tickEvery = zoom >= 2 ? 1 : zoom >= 1 ? 5 : zoom >= 0.5 ? 10 : 30 // seconds
    const ticks = []
    for (let t = 0; t <= (duration || 60); t += tickEvery) {
      const x = timeToPixels(t, zoom, PX_PER_SEC)
      ticks.push(
        <div key={t} className="absolute flex flex-col items-center" style={{ left: x }}>
          <div className="h-2 w-px bg-white/20" />
          <span className="text-[9px] text-text-disabled font-mono mt-0.5 whitespace-nowrap">
            {formatTime(t)}
          </span>
        </div>
      )
    }
    return ticks
  }

  const playheadX = timeToPixels(currentTime, zoom, PX_PER_SEC)

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
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/[0.04] shrink-0 bg-zinc-900 select-none w-full relative h-9">
        {/* Left: Spacer to maintain layout balance */}
        <div className="w-20" />

        {/* Center: Empty label area */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex items-center gap-1 z-10">
          <span className="text-[9px] font-semibold uppercase tracking-widest text-zinc-600 select-none">Timeline</span>
        </div>

        {/* Right: Zoom Controls */}
        <div className="flex items-center justify-end gap-1.5 z-10">
          <Tooltip content="Zoom out">
            <button
              className="h-6 w-6 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-white/8 transition-colors"
              onClick={zoomOut}
            >
              <ZoomOut size={13} />
            </button>
          </Tooltip>

          <div className="relative flex items-center group/zoom">
            {/* Tooltip bubble */}
            <div
              className={cn(
                "absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-0.5 rounded bg-zinc-900 border border-zinc-700 text-[10px] font-mono font-bold text-white shadow-md pointer-events-none transition-all duration-150 z-30 whitespace-nowrap",
                (showZoomTooltip || isDraggingSlider)
                  ? "opacity-100 scale-100 translate-y-0"
                  : "opacity-0 scale-90 translate-y-1"
              )}
            >
              {Math.round(zoom * 100)}%
              {/* Tooltip arrow */}
              <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-[1px] w-0 h-0 border-l-[4px] border-r-[4px] border-t-[4px] border-l-transparent border-r-transparent border-t-zinc-700" />
              <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-[2px] w-0 h-0 border-l-[4px] border-r-[4px] border-t-[4px] border-l-transparent border-r-transparent border-t-zinc-900" />
            </div>

            <input
              type="range"
              min="0.25"
              max="8"
              step="0.05"
              value={zoom}
              onChange={(e) => setZoom(parseFloat(e.target.value))}
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

          <Tooltip content="Zoom in">
            <button
              className="h-6 w-6 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-white/8 transition-colors"
              onClick={zoomIn}
            >
              <ZoomIn size={13} />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Main Timeline Area (Ruler + Tracks + Splitters) */}
      <div className="flex-1 min-h-0 flex flex-col relative">
        {/* Ruler Row */}
        <div className="flex h-7 bg-zinc-950/30 border-b border-white/[0.04] shrink-0 select-none relative">
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
              {renderRulerTicks()}
              {/* Playhead on ruler */}
              <div
                className="absolute top-0 bottom-0 w-px bg-status-error pointer-events-none z-30"
                style={{ left: playheadX, boxShadow: '0 0 6px rgba(239,68,68,0.8)' }}
              >
                <div className="w-0 h-0 border-l-[5px] border-r-[5px] border-t-[6px] border-l-transparent border-r-transparent border-t-status-error absolute -top-0 left-1/2 -translate-x-1/2" />
                {/* Floating Playhead Timestamp Badge */}
                <div className="absolute top-[6px] left-1/2 -translate-x-1/2 bg-zinc-900 border border-red-500/50 px-1.5 py-0.5 rounded-sm font-mono text-[10px] text-red-400 font-semibold shadow-md whitespace-nowrap select-none z-40">
                  {formatPlayheadTime(currentTime)}
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
            className="relative"
            style={{ width: `calc(${totalWidth}px + var(--speaker-width))`, minHeight: '100%' }}
            onMouseDown={onTracksMouseDown}
          >
            {/* Playhead line through all tracks */}
            <div
              className="playhead-line"
              style={{ left: `calc(var(--speaker-width) + ${playheadX}px)` }}
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
              style={{ height: 48 }}
            >
              {/* Track label (sticky left) */}
              <div
                className="sticky left-0 z-10 h-full flex items-center px-2 gap-1.5 shrink-0 bg-zinc-900/95 backdrop-blur-sm border-r border-white/[0.04]"
                style={{ float: 'left', width: 'var(--speaker-width)' }}
              >
                <div
                  className="h-2 w-2 rounded-full shrink-0 bg-sky-500"
                />
                <span className="text-xs text-text-muted truncate grow min-w-0 pr-1 font-semibold">
                  Background Music
                </span>

                {/* Mute Track Control */}
                <div className="flex items-center gap-0.5 ml-auto shrink-0">
                  <button
                    onClick={() => toggleMuteTrack('__bgm__')}
                    className={cn(
                      "h-5 w-5 rounded text-[10px] font-bold flex items-center justify-center border transition-all select-none",
                      mutedTrackIds['__bgm__']
                        ? "bg-amber-500/20 text-amber-500 border-amber-500/30 hover:bg-amber-500/30"
                        : "bg-transparent text-text-disabled border-transparent hover:bg-white/5 hover:text-text-muted"
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
              style={{ height: 48 }}
            >
              {/* Track label — Analyze button lives here (sticky, always visible) */}
              <div
                className="sticky left-0 z-10 h-full flex items-center px-2 gap-1.5 shrink-0 bg-zinc-900/95 backdrop-blur-sm border-r border-white/[0.04]"
                style={{ float: 'left', width: 'var(--speaker-width)' }}
              >
                <div className={cn(
                  "h-2 w-2 rounded-full shrink-0",
                  isStage1Processing ? "bg-emerald-500/30 animate-pulse"
                    : vocalsReplaced   ? "bg-zinc-600"
                    : "bg-emerald-500"
                )} />
                <div className="flex flex-col min-w-0 grow pr-1">
                  <span className="text-xs text-text-muted truncate font-semibold leading-tight">
                    {vocalsReplaced ? 'Orig. Vocals' : 'Vocals'}
                  </span>
                  {vocalsReplaced && (
                    <span className="text-[8px] text-zinc-600 truncate leading-tight">replaced by TTS</span>
                  )}
                </div>
                <div className="flex items-center gap-1 ml-auto shrink-0">
                  {/* M button — always visible when stems exist so user can toggle at any time */}
                  {!isStage1Processing && (
                    <button
                      onClick={() => toggleMuteTrack('__vocals__')}
                      className={cn(
                        "h-5 w-5 rounded text-[10px] font-bold flex items-center justify-center border transition-all select-none",
                        mutedTrackIds['__vocals__']
                          ? "bg-amber-500/20 text-amber-500 border-amber-500/30 hover:bg-amber-500/30"
                          : "bg-transparent text-text-disabled border-transparent hover:bg-white/5 hover:text-text-muted"
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
                        placeholder="max spk"
                        title="Max speakers (optional) — caps over-detection. Leave blank for auto."
                        disabled={analyzing}
                        className="w-14 px-1.5 py-0.5 rounded-full bg-zinc-800 border border-zinc-700 text-zinc-200 text-[9px] focus:outline-none focus:border-emerald-500 disabled:opacity-60"
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

            {/* Speaker tracks */}
            {speakerIds.map((speakerId, trackIdx) => {
              const speaker = speakers.find((s) => s.id === speakerId)
              const trackSegments = segments.filter((s) => (s.speaker_id ?? '__none__') === speakerId)
              const color = speakerColorMap.get(speakerId) ?? getSpeakerColor(trackIdx)

              return (
                <div
                  key={speakerId}
                  className="relative border-b border-timeline-grid"
                  style={{ height: 48 }}
                >
                  {/* Track label (sticky left) */}
                  <div
                    className="sticky left-0 z-10 h-full flex items-center px-2 gap-1.5 shrink-0 bg-zinc-900/95 backdrop-blur-sm border-r border-white/[0.04]"
                    style={{ float: 'left', width: 'var(--speaker-width)' }}
                  >
                    <div
                      className="h-2 w-2 rounded-full shrink-0"
                      style={{ background: color }}
                    />
                    <span className="text-xs text-text-muted truncate grow min-w-0 pr-1">
                      {speaker?.name ?? speaker?.label ?? `S${trackIdx + 1}`}
                    </span>

                    {/* Mute & Solo Track Controls */}
                    <div className="flex items-center gap-0.5 ml-auto shrink-0">
                      <button
                        onClick={() => toggleMuteTrack(speakerId)}
                        className={cn(
                          "h-5 w-5 rounded text-[10px] font-bold flex items-center justify-center border transition-all select-none",
                          mutedTrackIds[speakerId ?? '__none__']
                            ? "bg-amber-500/20 text-amber-500 border-amber-500/30 hover:bg-amber-500/30"
                            : "bg-transparent text-text-disabled border-transparent hover:bg-white/5 hover:text-text-muted"
                        )}
                        title="Mute Track"
                      >
                        M
                      </button>
                      <button
                        onClick={() => toggleSoloTrack(speakerId)}
                        className={cn(
                          "h-5 w-5 rounded text-[10px] font-bold flex items-center justify-center border transition-all select-none",
                          soloedTrackIds[speakerId ?? '__none__']
                            ? "bg-emerald-500/20 text-emerald-500 border-emerald-500/30 hover:bg-emerald-500/30"
                            : "bg-transparent text-text-disabled border-transparent hover:bg-white/5 hover:text-text-muted"
                        )}
                        title="Solo Track"
                      >
                        S
                      </button>
                    </div>
                  </div>

                  {/* Segments */}
                  <div
                    className="absolute top-0 bottom-0 right-0"
                    style={{ left: 'var(--speaker-width)' }}
                  >
                    {trackSegments.map((seg) => {
                      const isActive = seg.id === activeSegmentId
                      const isApproved = seg.is_approved

                      return (
                        <InteractiveSegment
                          key={seg.id}
                          seg={seg}
                          color={color}
                          isActive={isActive}
                          isApproved={isApproved}
                          zoom={zoom}
                          duration={duration}
                          speakerIds={speakerIds}
                          allSegments={segments}
                          PX_PER_SEC={PX_PER_SEC}
                          scrollRef={scrollRef}
                          updateSegmentPosition={updateSegmentPosition}
                          onUpdateBackend={(id, startTime, endTime, speakerId) => {
                            updateSegment({
                              segmentId: id,
                              data: {
                                start_time: startTime,
                                end_time: endTime,
                                speaker_id: speakerId
                              }
                            })
                          }}
                          onSelect={() => {
                            setCurrentTime(seg.start_time)
                            setActiveSegment(seg.id)
                            setInspectorMode('audio_clip_settings')
                            setFocusedTimelineItemId(seg.id)
                          }}
                          onDelete={(id) => {
                            deleteSegment({ segmentId: id, jobId: jobId! })
                            setActiveSegment(null)
                          }}
                          onRegenerate={jobId ? (id) => regenerateSegment({ segmentId: id, jobId }) : undefined}
                          isPersisting={isUpdatingSegment && updateVariables?.segmentId === seg.id}
                          isRegenerating={isRegenerating && regenVariables?.segmentId === seg.id}
                          mutedTrackIds={mutedTrackIds}
                          soloedTrackIds={soloedTrackIds}
                          volume={volume}
                        />
                      )
                    })}
                  </div>
                </div>
              )
            })}

            {/* Empty state */}
            {segments.length === 0 && (
              <div className="flex flex-col items-center justify-center h-24 text-text-disabled text-xs gap-3 w-full">
                {job && isJobRunning(job.status) ? (
                  <>
                    <div className="flex items-center gap-2 text-brand-300 font-medium">
                      <Loader2 size={14} className="animate-spin" />
                      <span>{getJobStatusConfig(job.status).description}</span>
                    </div>
                    {/* Progress bar */}
                    <div className="w-64 h-1.5 rounded-full overflow-hidden bg-zinc-800 border border-zinc-700/50">
                      <div
                        className="h-full bg-gradient-to-r from-brand to-accent transition-all duration-300"
                        style={{
                          width: `${
                            job.status === 'extracting' ? 10
                            : job.status === 'separating' ? 20
                            : job.status === 'diarizing' ? 35
                            : job.status === 'transcribing' ? 55
                            : job.status === 'translating' ? 75
                            : job.status === 'synthesizing' ? 85
                            : job.status === 'mixing' ? 95
                            : 0
                          }%`
                        }}
                      />
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
  isApproved: boolean
  zoom: number
  duration: number
  speakerIds: string[]
  allSegments: Segment[]
  PX_PER_SEC: number
  scrollRef: React.RefObject<HTMLDivElement | null>
  updateSegmentPosition: (
    id: string,
    start_time: number,
    end_time: number,
    speaker_id: string | null,
    tts_duration_secs?: number,
    tts_audio_path?: string
  ) => void
  onUpdateBackend: (id: string, startTime: number, endTime: number, speakerId: string | null) => void
  onSelect: () => void
  onDelete: (id: string) => void
  onRegenerate?: (id: string) => void
  isPersisting?: boolean
  isRegenerating?: boolean
  mutedTrackIds: Record<string, boolean>
  soloedTrackIds: Record<string, boolean>
  volume: number
}

function InteractiveSegment({
  seg, color, isActive, isApproved, zoom, duration, speakerIds, allSegments, PX_PER_SEC, scrollRef,
  updateSegmentPosition, onUpdateBackend, onSelect, onDelete, onRegenerate,
  isPersisting, isRegenerating,
  mutedTrackIds, soloedTrackIds, volume
}: InteractiveSegmentProps) {
  const elementRef = useRef<HTMLDivElement>(null)

  const left = timeToPixels(seg.start_time, zoom, PX_PER_SEC)
  const width = Math.max(4, timeToPixels(seg.end_time - seg.start_time, zoom, PX_PER_SEC))

  // Heuristic safeguard check
  const estimatedDuration = seg.khmer_text ? seg.khmer_text.length * 0.12 : 1.0
  const currentDuration = seg.end_time - seg.start_time
  const isTooFast = currentDuration < estimatedDuration

  // Cache-busted URL — the backend overwrites the same file path on every
  // re-synthesis, so a version query param (tts_duration_secs changes on
  // every real synth) is what actually busts the browser + peaksCache.
  const versionedAudioPath = seg.tts_audio_path
    ? `${seg.tts_audio_path}?v=${seg.tts_duration_secs ?? 0}`
    : undefined

  // Native Browser Audio Playback — real audio only, no mock/simulation fallback
  const playAudio = () => {
    if (!seg.tts_audio_path) return
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

    const maxTimelineWidth = timeToPixels(duration || 60, zoom, PX_PER_SEC)

    element.style.zIndex = '50'
    element.style.opacity = '0.9'

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const deltaX = moveEvent.clientX - startX
      const deltaY = moveEvent.clientY - startY

      if (actionType === 'drag') {
        let newLeft = startLeft + deltaX
        newLeft = Math.max(0, Math.min(maxTimelineWidth - startWidth, newLeft))
        element.style.left = `${newLeft}px`
        element.style.transform = `translateY(${deltaY}px)`
      } else if (actionType === 'resize-left') {
        let newLeft = startLeft + deltaX
        let newWidth = startWidth - deltaX

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
        let newWidth = startWidth + deltaX

        const minWidthPx = timeToPixels(0.1, zoom, PX_PER_SEC)
        const maxWidthPx = maxTimelineWidth - startLeft
        newWidth = Math.max(minWidthPx, Math.min(maxWidthPx, newWidth))
        element.style.width = `${newWidth}px`
      }
    }

    const handlePointerUp = (upEvent: PointerEvent) => {
      element.releasePointerCapture(upEvent.pointerId)
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)

      element.style.zIndex = ''
      element.style.opacity = ''
      element.style.transform = ''

      const finalLeft = parseFloat(element.style.left)
      const finalWidth = parseFloat(element.style.width)

      let newStartTime = pixelsToTime(finalLeft, zoom, PX_PER_SEC)
      let newEndTime = pixelsToTime(finalLeft + finalWidth, zoom, PX_PER_SEC)

      let finalSpeakerId = seg.speaker_id ?? null
      if (actionType === 'drag') {
        const containerRect = container.getBoundingClientRect()
        const relativeY = upEvent.clientY - containerRect.top + container.scrollTop
        const trackIdx = Math.floor(relativeY / 48)
        const clampedIdx = Math.max(0, Math.min(speakerIds.length - 1, trackIdx))
        const targetSpeakerId = speakerIds[clampedIdx]
        finalSpeakerId = targetSpeakerId === '__none__' ? null : targetSpeakerId
      }

      // Prevent overlap with whatever's already on the destination track —
      // real editors never let two clips on the same track share time range.
      const targetTrackKey = finalSpeakerId ?? '__none__'
      const neighbors = allSegments.filter(
        (s) => s.id !== seg.id && (s.speaker_id ?? '__none__') === targetTrackKey
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

      // Position/speaker changes persist immediately — trimming or moving a
      // segment never touches its audio (matches real NLE trim behavior;
      // VideoPlayer's playback-rate auto-fit absorbs any duration mismatch).
      updateSegmentPosition(seg.id, newStartTime, newEndTime, finalSpeakerId)
      onUpdateBackend(seg.id, newStartTime, newEndTime, finalSpeakerId)
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
  }

  return (
    <div
      ref={elementRef}
      className={cn(
        'timeline-segment absolute top-[5px] bottom-[5px] cursor-grab select-none overflow-hidden transition-all',
        isActive && 'active border-purple-500 outline-2 outline-purple-500 ring-2 ring-purple-500/20'
      )}
      style={{
        left,
        width,
        background: isApproved
          ? `linear-gradient(180deg, ${hexToRgba(color, 0.8)} 0%, ${hexToRgba(color, 0.95)} 100%)`
          : `linear-gradient(180deg, ${hexToRgba(color, 0.35)} 0%, ${hexToRgba(color, 0.45)} 100%)`,
        borderLeft: `3px solid ${isTooFast ? '#EF4444' : color}`,
        borderTop: isTooFast ? '2px solid #EF4444' : `1px solid ${hexToRgba(color, isApproved ? 0.8 : 0.4)}`,
        borderRight: isTooFast ? '2px solid #EF4444' : `1px solid ${hexToRgba(color, isApproved ? 0.6 : 0.3)}`,
        borderBottom: isTooFast ? '2px solid #EF4444' : `1px solid ${hexToRgba(color, isApproved ? 0.6 : 0.3)}`,
        borderRadius: '6px',
        boxShadow: isTooFast
          ? '0 0 14px rgba(239, 68, 68, 0.45)'
          : (isActive ? `0 0 12px ${hexToRgba('#7C3AED', 0.6)}` : 'none'),
        outline: isActive ? '2px solid #7C3AED' : 'none',
        outlineOffset: '-2px',
      }}
      onPointerDown={(e) => {
        if (e.button === 0) {
          handlePointerDown(e, 'drag')
          onSelect()
        }
      }}
      onMouseDown={(e) => {
        e.stopPropagation()
      }}
      onDoubleClick={(e) => {
        e.stopPropagation()
        playAudio()
      }}
      title={isTooFast ? `${seg.khmer_text || seg.source_text} (Speed Up Required)` : (seg.khmer_text || seg.source_text)}
    >
      {/* Left Trim Handle */}
      <div
        className="absolute left-0 top-0 bottom-0 w-2.5 cursor-col-resize z-20 flex items-center justify-center group/left hover:bg-white/10 active:bg-white/20 transition-all"
        onPointerDown={(e) => {
          if (e.button === 0) {
            e.stopPropagation()
            handlePointerDown(e, 'resize-left')
          }
        }}
      >
        <div className="w-[2px] h-3.5 bg-white/25 group-hover/left:bg-white/70 group-active/left:bg-white rounded transition-colors" />
      </div>

      {/* Segment Text */}
      {width > 50 && (
        <span
          className={cn(
            "absolute left-3.5 right-3.5 top-1 flex items-center gap-1 text-[10px] font-semibold overflow-hidden whitespace-nowrap pointer-events-none select-none",
            isTooFast ? "text-red-400 font-bold" : "text-white/92"
          )}
          style={{ textShadow: '0 1px 2px rgba(0,0,0,0.6)' }}
        >
          {isTooFast && (
            <AlertTriangle size={10} className="shrink-0 text-red-500 animate-pulse" />
          )}
          <span className="truncate">{seg.khmer_text || seg.source_text}</span>
        </span>
      )}

      {/* Real audio waveform — decoded from the actual TTS clip, cache-busted
          so a re-synthesis of the same file path is never shown stale */}
      {versionedAudioPath && (
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

      {/* Persisting dot — reflects the real in-flight position/speaker PATCH */}
      {isPersisting && (
        <div className="absolute top-1 left-1 h-1.5 w-1.5 rounded-full bg-white/70 animate-pulse pointer-events-none" title="Saving…" />
      )}

      {/* Approved Indicator (hidden when segment is active — delete button takes that spot) */}
      {isApproved && !isActive && (
        <div
          className="absolute top-1 right-2.5 h-1.5 w-1.5 rounded-full bg-emerald-400 border border-black/20 shadow-sm pointer-events-none"
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

      {/* Right Trim Handle */}
      <div
        className="absolute right-0 top-0 bottom-0 w-2.5 cursor-col-resize z-20 flex items-center justify-center group/right hover:bg-white/10 active:bg-white/20 transition-all"
        onPointerDown={(e) => {
          if (e.button === 0) {
            e.stopPropagation()
            handlePointerDown(e, 'resize-right')
          }
        }}
      >
        <div className="w-[2px] h-3.5 bg-white/25 group-hover/right:bg-white/70 group-active/right:bg-white rounded transition-colors" />
      </div>
    </div>
  )
}

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

const peaksCache: Record<string, number[]> = {}

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

interface SegmentWaveformProps {
  audioPath?: string
  width: number
  color?: string   // hex or CSS color — defaults to white (matches segment block)
  loading?: boolean // external loading hint (e.g. stem is being generated)
}

function SegmentWaveform({ audioPath, width, color = 'white', loading: externalLoading }: SegmentWaveformProps) {
  const [peaks, setPeaks] = useState<number[] | null>(() => {
    return (audioPath && peaksCache[audioPath]) || null
  })
  const [error,    setError]   = useState(false)
  const [fetching, setFetching] = useState(false)

  useEffect(() => {
    let active = true

    if (!audioPath) {
      requestAnimationFrame(() => {
        if (active) setPeaks(null)
      })
      return () => {
        active = false
      }
    }
    if (peaksCache[audioPath]) {
      requestAnimationFrame(() => {
        if (active) setPeaks(peaksCache[audioPath])
      })
      return () => {
        active = false
      }
    }

    const fetchAndDecode = async () => {
      if (active) setFetching(true)
      try {
        const url = audioPath.startsWith('/') ? audioPath : `/${audioPath}`
        const res = await fetch(url)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const arrayBuffer = await res.arrayBuffer()
        const ctx = getSharedAudioContext()
        if (!ctx) return

        // decodeAudioData can throw on corrupt/unsupported files
        const audioBuffer = await ctx.decodeAudioData(arrayBuffer)
        const numPeaks = Math.max(20, Math.floor(width / 3.5))
        const extracted = extractPeaks(audioBuffer, numPeaks)

        peaksCache[audioPath] = extracted
        if (active) { setPeaks(extracted); setFetching(false) }
      } catch (err) {
        console.warn('Waveform load failed for', audioPath, err)
        if (active) { setError(true); setFetching(false) }
      }
    }

    fetchAndDecode()
    return () => { active = false }
  }, [audioPath, width])

  const isLoading = externalLoading || (fetching && !peaks && !error)

  // Loading state — pulsing bars so users know it's working
  if (isLoading) {
    return (
      <div className="absolute inset-x-3 bottom-1 h-4 flex items-end gap-[2px] pointer-events-none">
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

  // Error or still null — faint dashed line
  if (error || !peaks) {
    return (
      <div className="absolute inset-x-2 bottom-1.5 top-3.5 flex items-center justify-center opacity-[0.08] pointer-events-none">
        <div className="w-full border-t border-dashed" style={{ borderColor: color }} />
      </div>
    )
  }

  return (
    <svg
      className="absolute inset-x-2.5 bottom-1 h-4.5 w-[calc(100%-20px)] pointer-events-none opacity-50"
      preserveAspectRatio="none"
      viewBox={`0 0 ${peaks.length} 1`}
    >
      {peaks.map((peak, i) => {
        const barHeight = Math.max(0.1, peak * 0.85)
        const y = (1 - barHeight) / 2
        return (
          <rect
            key={i}
            x={i}
            y={y}
            width={0.65}
            height={barHeight}
            fill={color}
            rx={0.15}
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

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    setReady(false)
    setError(false)

    const url = audioUrl.startsWith('/') ? audioUrl : `/${audioUrl}`

    const ws = WaveSurfer.create({
      container,
      url,
      waveColor: color,
      progressColor: color,
      height: 30,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      interact: false,
      normalize: true,
      hideScrollbar: true,
      minPxPerSec: pxPerSec,
    })

    ws.on('ready', () => setReady(true))
    ws.on('error', () => setError(true))
    wsRef.current = ws

    return () => { ws.destroy(); wsRef.current = null }
  }, [audioUrl, color])

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

  if (error) {
    return (
      <div className="absolute inset-x-2 inset-y-1 flex items-center opacity-10 pointer-events-none">
        <div className="w-full border-t border-dashed" style={{ borderColor: color }} />
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
      <div
        ref={containerRef}
        className="absolute inset-0 overflow-hidden"
        style={{ opacity: ready ? 0.8 : 0 }}
      />
    </>
  )
}
