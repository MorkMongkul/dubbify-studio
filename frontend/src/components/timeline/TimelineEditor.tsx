// src/components/timeline/TimelineEditor.tsx
import { useRef, useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ZoomIn, ZoomOut } from 'lucide-react'
import type { Segment, Speaker } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { timeToPixels, pixelsToTime, formatTime, getSpeakerColor, hexToRgba, cn } from '@/lib/utils'
import { Tooltip } from '@/components/ui/Tooltip'

const PX_PER_SEC = 100 // base pixels per second at zoom=1

interface TimelineEditorProps {
  segments: Segment[]
  speakers: Speaker[]
  duration: number
  className?: string
}

export function TimelineEditor({ segments, speakers, duration, className }: TimelineEditorProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const rulerRef  = useRef<HTMLDivElement>(null)
  const [isDragging, setIsDragging] = useState(false)

  const {
    currentTime, zoom, activeSegmentId,
    setCurrentTime, setActiveSegment, zoomIn, zoomOut, resetZoom,
  } = useEditorStore()

  const totalWidth = Math.max(timeToPixels(duration || 60, zoom, PX_PER_SEC), 800)

  // Group segments by speaker
  const speakerIds = [...new Set(segments.map((s) => s.speaker_id ?? '__none__'))]

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
    <div className={cn('flex flex-col bg-timeline-bg border-t border-border', className)}>
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border shrink-0 bg-surface-1">
        <span className="text-xs font-semibold text-text-muted uppercase tracking-wider">Timeline</span>
        <div className="flex items-center gap-1">
          <Tooltip content="Zoom out">
            <button
              className="h-6 w-6 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-white/8 transition-colors"
              onClick={zoomOut}
            >
              <ZoomOut size={12} />
            </button>
          </Tooltip>
          <button
            className="h-6 px-2 rounded text-xs font-mono text-text-muted hover:text-text-primary hover:bg-white/8 transition-colors"
            onClick={resetZoom}
          >
            {Math.round(zoom * 100)}%
          </button>
          <Tooltip content="Zoom in">
            <button
              className="h-6 w-6 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-white/8 transition-colors"
              onClick={zoomIn}
            >
              <ZoomIn size={12} />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* Ruler (fixed top) */}
      <div
        ref={rulerRef}
        className="h-7 overflow-hidden relative bg-timeline-ruler border-b border-border shrink-0"
        style={{ overflowX: 'hidden' }}
      >
        <div
          className="relative h-full cursor-crosshair select-none"
          style={{ width: totalWidth + 200 }}
          onMouseDown={onRulerMouseDown}
        >
          {renderRulerTicks()}
          {/* Playhead on ruler */}
          <div
            className="absolute top-0 bottom-0 w-px bg-status-error pointer-events-none"
            style={{ left: playheadX, boxShadow: '0 0 6px rgba(239,68,68,0.8)' }}
          >
            <div className="w-0 h-0 border-l-[5px] border-r-[5px] border-t-[6px] border-l-transparent border-r-transparent border-t-status-error absolute -top-0 left-1/2 -translate-x-1/2" />
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
        <div className="relative" style={{ width: totalWidth + 200, minHeight: '100%' }}>
          {/* Playhead line through all tracks */}
          <div
            className="playhead-line"
            style={{ left: playheadX }}
          />

          {/* Grid lines */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              backgroundImage: `repeating-linear-gradient(90deg, rgba(255,255,255,0.03) 0px, rgba(255,255,255,0.03) 1px, transparent 1px, transparent ${PX_PER_SEC * zoom}px)`,
            }}
          />

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
                  className="sticky left-0 z-10 h-full flex items-center px-2 gap-1.5 w-24 shrink-0 bg-timeline-bg/95 backdrop-blur-sm border-r border-border"
                  style={{ float: 'left' }}
                >
                  <div
                    className="h-2 w-2 rounded-full shrink-0"
                    style={{ background: color }}
                  />
                  <span className="text-xs text-text-muted truncate">
                    {speaker?.name ?? speaker?.label ?? `S${trackIdx + 1}`}
                  </span>
                </div>

                {/* Segments */}
                <div className="absolute inset-0 pl-24">
                  {trackSegments.map((seg) => {
                    const left  = timeToPixels(seg.start_time, zoom, PX_PER_SEC)
                    const width = Math.max(4, timeToPixels(seg.end_time - seg.start_time, zoom, PX_PER_SEC))
                    const isActive = seg.id === activeSegmentId
                    const isApproved = seg.is_approved

                    return (
                      <motion.div
                        key={seg.id}
                        className={cn(
                          'timeline-segment',
                          isActive && 'active',
                        )}
                        style={{
                          left,
                          width,
                          background: isApproved
                            ? hexToRgba(color, 0.9)
                            : hexToRgba(color, 0.5),
                          borderLeft: `2px solid ${color}`,
                          borderTop: isApproved ? `1px solid ${hexToRgba(color, 0.6)}` : 'none',
                        }}
                        whileHover={{ scaleY: 1.08 }}
                        transition={{ duration: 0.1 }}
                        onClick={() => {
                          setCurrentTime(seg.start_time)
                          setActiveSegment(seg.id)
                        }}
                        title={seg.source_text}
                      >
                        {width > 50 && (
                          <span
                            className="absolute inset-0 px-1.5 flex items-center text-[10px] font-medium overflow-hidden whitespace-nowrap"
                            style={{ color: 'rgba(255,255,255,0.85)' }}
                          >
                            {seg.source_text}
                          </span>
                        )}
                        {isApproved && (
                          <div
                            className="absolute top-0.5 right-0.5 h-1.5 w-1.5 rounded-full bg-emerald-400"
                          />
                        )}
                      </motion.div>
                    )
                  })}
                </div>
              </div>
            )
          })}

          {/* Empty state */}
          {segments.length === 0 && (
            <div className="flex items-center justify-center h-24 text-text-disabled text-xs">
              No segments yet — process a video to populate the timeline
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
