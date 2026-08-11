// src/components/video/OverlayCanvas.tsx
// CapCut-style draggable/resizable layers on top of the video preview —
// dropped images, plain shape boxes, and the burned-in subtitle block.
// Rendered as a sibling of <video> inside VideoPlayer's own aspect-ratio-sized
// container, so fractional (0-1) x/y/width/height map directly onto real video
// pixels — see video_overlay.py / mix_dubbed_audio for the export counterpart.
import { useRef, useState, useLayoutEffect } from 'react'
import { X } from 'lucide-react'
import type { Overlay, Segment } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { useUpdateOverlay, useDeleteOverlay } from '@/hooks/useApi'
import { cn } from '@/lib/utils'

// Translucent preview stand-ins for the export-side background chip colors
// (matches video_overlay.py's _BACKGROUND_ALPHA ≈ 75% opacity) — alpha is
// baked into the color itself rather than the element's own `opacity` so
// the text drawn on top of it stays fully solid, not faded along with it.
const BACKGROUND_RGBA: Record<string, string> = {
  white: 'rgba(255,255,255,0.75)',
  yellow: 'rgba(255,224,32,0.75)',
  black: 'rgba(0,0,0,0.75)',
}

// Mirrors video_overlay.py — keep these in step with the renderer.
const MAX_BLOCK_FRACTION = 0.40   // block never covers more of the frame than this
const MIN_FONT_SCALE = 0.45       // matches _MIN_FONT_SCALE
const LINE_SPACING = 1.18         // matches _LINE_SPACING
const FALLBACK_VIDEO_WIDTH = 1080 // until loadedmetadata lands

interface OverlayCanvasProps {
  overlays: Overlay[]
  segments: Segment[]
  jobId: string
  currentTime: number
  /** Intrinsic width of the clip in real pixels — font_size is expressed in
   *  those, so the preview scales by canvasWidth / videoPixelWidth. */
  videoPixelWidth?: number
}

export function OverlayCanvas({ overlays, segments, jobId, currentTime, videoPixelWidth }: OverlayCanvasProps) {
  const canvasRef = useRef<HTMLDivElement>(null)
  const selectedOverlayId = useEditorStore((s) => s.selectedOverlayId)
  const setSelectedOverlay = useEditorStore((s) => s.setSelectedOverlay)
  const updateOverlay = useUpdateOverlay()
  const deleteOverlay = useDeleteOverlay()

  const activeSubtitleText = (() => {
    const seg = segments.find((s) => currentTime >= s.start_time && currentTime <= s.end_time)
    return seg?.khmer_text || ''
  })()

  // Measured HERE, not inside OverlayBox: refs attach bottom-up, so a child's
  // layout effect runs before this parent's ref exists — measuring canvasRef
  // from the child silently read null forever and left the scale at 0.
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 })
  useLayoutEffect(() => {
    const el = canvasRef.current
    if (!el) return
    const apply = () => {
      const r = el.getBoundingClientRect()
      setCanvasSize((prev) =>
        Math.abs(prev.width - r.width) < 0.5 && Math.abs(prev.height - r.height) < 0.5
          ? prev
          : { width: r.width, height: r.height }
      )
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div
      ref={canvasRef}
      className="absolute inset-0 z-20"
      onPointerDown={(e) => {
        if (e.target === canvasRef.current) setSelectedOverlay(null)
      }}
    >
      {[...overlays].sort((a, b) => a.z_index - b.z_index).map((ov) => (
        <OverlayBox
          key={ov.id}
          overlay={ov}
          canvasRef={canvasRef}
          isSelected={selectedOverlayId === ov.id}
          text={ov.type === 'subtitle' ? activeSubtitleText : undefined}
          videoPixelWidth={videoPixelWidth || FALLBACK_VIDEO_WIDTH}
          canvasSize={canvasSize}
          onSelect={() => setSelectedOverlay(ov.id)}
          onCommit={(patch) => updateOverlay.mutate({ id: ov.id, jobId, data: patch })}
          onDelete={() => {
            deleteOverlay.mutate({ id: ov.id, jobId })
            if (selectedOverlayId === ov.id) setSelectedOverlay(null)
          }}
        />
      ))}
    </div>
  )
}

type ResizeMode = 'corner' | 'left' | 'right'

interface OverlayBoxProps {
  overlay: Overlay
  canvasRef: React.RefObject<HTMLDivElement | null>
  isSelected: boolean
  text?: string
  videoPixelWidth: number
  canvasSize: { width: number; height: number }
  onSelect: () => void
  onCommit: (patch: { x?: number; y?: number; width?: number; height?: number }) => void
  onDelete: () => void
}

function OverlayBox({
  overlay, canvasRef, isSelected, text, videoPixelWidth, canvasSize, onSelect, onCommit, onDelete,
}: OverlayBoxProps) {
  const boxRef = useRef<HTMLDivElement>(null)
  const mirrorRef = useRef<HTMLDivElement>(null)
  const isSubtitle = overlay.type === 'subtitle'

  // ── Subtitle sizing, mirroring video_overlay.py ────────────────────────
  // font_size is in real video pixels; scale it to the on-screen canvas so the
  // preview shows the size the export will actually burn in. (The old preview
  // derived the font from the box's own height, which meant the Size control
  // did nothing here and resizing the box silently changed preview-only text
  // size — preview and export disagreed completely.)
  const previewScale = canvasSize.width > 0 ? canvasSize.width / videoPixelWidth : 0
  const baseFontPx = Math.max(1, overlay.font_size * previewScale)

  // Backstop shrink, same as the renderer: measure the block at its natural
  // size in a hidden mirror (stable — never re-measures its own output, so it
  // can't oscillate) and scale down only if it would swallow the frame.
  const [fitScale, setFitScale] = useState(1)
  useLayoutEffect(() => {
    if (!isSubtitle) return
    const el = mirrorRef.current
    if (!el || !canvasSize.height || !baseFontPx) return
    const cap = canvasSize.height * MAX_BLOCK_FRACTION
    const natural = el.offsetHeight
    setFitScale(natural > cap && natural > 0 ? Math.max(MIN_FONT_SCALE, cap / natural) : 1)
  }, [isSubtitle, text, baseFontPx, overlay.width, canvasSize.height, canvasSize.width])

  const fontPx = baseFontPx * fitScale
  const strokePx = Math.max(1, fontPx / 14)   // renderer: max(2, size // 14)

  const handleDragPointerDown = (e: React.PointerEvent, mode: 'move' | ResizeMode) => {
    e.stopPropagation()
    e.preventDefault()
    onSelect()
    const canvas = canvasRef.current
    const el = boxRef.current
    if (!canvas || !el) return
    const canvasRect = canvas.getBoundingClientRect()
    const startX = e.clientX
    const startY = e.clientY
    const startLeft = overlay.x
    const startTop = overlay.y
    const startWidth = overlay.width
    const startHeight = overlay.height

    el.setPointerCapture(e.pointerId)

    let liveX = startLeft, liveY = startTop, liveW = startWidth, liveH = startHeight

    const onMove = (ev: PointerEvent) => {
      const dxFrac = (ev.clientX - startX) / canvasRect.width
      const dyFrac = (ev.clientY - startY) / canvasRect.height

      if (mode === 'move') {
        liveX = Math.max(0, Math.min(1 - startWidth, startLeft + dxFrac))
        liveY = Math.max(0, Math.min(1 - startHeight, startTop + dyFrac))
        el.style.left = `${liveX * 100}%`
        if (isSubtitle) el.style.bottom = `${(1 - (liveY + startHeight)) * 100}%`
        else el.style.top = `${liveY * 100}%`
      } else if (mode === 'left') {
        // Drag the left edge: x moves, the right edge stays put.
        const right = startLeft + startWidth
        liveX = Math.max(0, Math.min(right - 0.05, startLeft + dxFrac))
        liveW = right - liveX
        el.style.left = `${liveX * 100}%`
        el.style.width = `${liveW * 100}%`
      } else if (mode === 'right') {
        liveW = Math.max(0.05, Math.min(1 - startLeft, startWidth + dxFrac))
        el.style.width = `${liveW * 100}%`
      } else {
        liveW = Math.max(0.03, Math.min(1 - startLeft, startWidth + dxFrac))
        liveH = Math.max(0.03, Math.min(1 - startTop, startHeight + dyFrac))
        el.style.width = `${liveW * 100}%`
        el.style.height = `${liveH * 100}%`
      }
    }

    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      if (mode === 'move') onCommit({ x: liveX, y: liveY })
      else if (mode === 'left') onCommit({ x: liveX, width: liveW })
      else if (mode === 'right') onCommit({ width: liveW })
      else onCommit({ width: liveW, height: liveH })
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  // Subtitles are anchored by the box's BOTTOM edge and size themselves to
  // their own text, growing upward — so a longer line never gets clipped and
  // the baseline never shifts between segments. Images/shapes keep the
  // literal rectangle they were given.
  const positionStyle: React.CSSProperties = isSubtitle
    ? {
        left: `${overlay.x * 100}%`,
        bottom: `${(1 - (overlay.y + overlay.height)) * 100}%`,
        width: `${overlay.width * 100}%`,
        height: 'auto',
      }
    : {
        left: `${overlay.x * 100}%`,
        top: `${overlay.y * 100}%`,
        width: `${overlay.width * 100}%`,
        height: `${overlay.height * 100}%`,
      }

  const textBlockStyle: React.CSSProperties = {
    color: overlay.color,
    fontSize: `${fontPx}px`,
    fontWeight: 700,
    lineHeight: LINE_SPACING,
    WebkitTextStroke: `${strokePx}px ${overlay.outline_color}`,
    paintOrder: 'stroke fill',
    wordBreak: 'break-word',
  }

  return (
    <div
      ref={boxRef}
      className={cn(
        'absolute cursor-move select-none',
        // No border/ring at all while unselected — it should look exactly
        // like the real video, with editing affordances only appearing once
        // this layer is actively being worked on.
        isSelected ? 'ring-2 ring-purple-500' : 'hover:ring-1 hover:ring-white/30'
      )}
      style={positionStyle}
      onPointerDown={(e) => handleDragPointerDown(e, 'move')}
    >
      {overlay.type === 'image' ? (
        <img
          src={overlay.media_url ?? undefined}
          alt=""
          draggable={false}
          className="w-full h-full object-fill pointer-events-none"
          style={{ opacity: overlay.opacity }}
        />
      ) : overlay.type === 'shape' ? (
        <div
          className="w-full h-full pointer-events-none"
          style={
            overlay.blur
              // backdrop-filter blurs whatever renders behind this element
              // (the video itself) — an exact live preview, not just an
              // approximation, since it's the same blur mechanism visually.
              ? { backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)' }
              : { background: overlay.color, opacity: overlay.opacity }
          }
        />
      ) : (
        <>
          {/* Hidden mirror at the UNSCALED font — measuring this instead of
              the visible block keeps the backstop-shrink calculation from
              feeding on its own output and oscillating. */}
          <div
            ref={mirrorRef}
            aria-hidden
            className="absolute left-0 top-0 w-full text-center pointer-events-none"
            style={{ ...textBlockStyle, fontSize: `${baseFontPx}px`, visibility: 'hidden' }}
          >
            {text || ' '}
          </div>

          <div className="w-full text-center pointer-events-none" style={textBlockStyle}>
            {text ? (
              overlay.background_color ? (
                <span
                  style={{
                    background: BACKGROUND_RGBA[overlay.background_color] ?? 'rgba(0,0,0,0.75)',
                    borderRadius: '0.3em',
                    padding: '0.1em 0.35em',
                    boxDecorationBreak: 'clone',
                    WebkitBoxDecorationBreak: 'clone',
                  } as React.CSSProperties}
                >
                  {text}
                </span>
              ) : text
            ) : isSelected ? (
              // No segment covers the current playhead right now — *not* the
              // overlay being unconfigured, every segment's translated text
              // still gets burned in at export regardless of where the
              // playhead sits. Only worth mentioning while actively editing
              // this layer; hidden the rest of the time so it never sits on
              // top of the video like a stray caption.
              <span style={{ color: 'rgba(255,255,255,0.4)', fontSize: '0.5em', fontWeight: 400, fontStyle: 'italic', WebkitTextStroke: '0' }}>
                No dialogue at this moment
              </span>
            ) : null}
          </div>
        </>
      )}

      {isSelected && (
        <>
          <button
            className="absolute -top-3 -right-3 h-5 w-5 rounded-full bg-red-500 hover:bg-red-400 flex items-center justify-center shadow z-10"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onDelete() }}
            title="Delete overlay"
          >
            <X size={11} className="text-white" strokeWidth={3} />
          </button>

          {isSubtitle ? (
            // Height is automatic, so the only thing to drag is the text
            // column's width — one handle per side, full height for an easy hit.
            <>
              <div
                className="absolute left-0 top-0 bottom-0 -ml-1 w-2 cursor-ew-resize flex items-center justify-center group/w"
                onPointerDown={(e) => handleDragPointerDown(e, 'left')}
                title="Drag to set the text column width"
              >
                <div className="h-8 w-1 rounded-full bg-purple-500 border border-white/70 group-hover/w:h-10 transition-all" />
              </div>
              <div
                className="absolute right-0 top-0 bottom-0 -mr-1 w-2 cursor-ew-resize flex items-center justify-center group/w"
                onPointerDown={(e) => handleDragPointerDown(e, 'right')}
                title="Drag to set the text column width"
              >
                <div className="h-8 w-1 rounded-full bg-purple-500 border border-white/70 group-hover/w:h-10 transition-all" />
              </div>
              {/* Anchor marker — the line the text always rests on */}
              <div className="absolute -bottom-px left-0 right-0 h-px bg-purple-400/70 pointer-events-none" />
            </>
          ) : (
            <div
              className="absolute -bottom-1.5 -right-1.5 h-3.5 w-3.5 rounded-sm bg-purple-500 border border-white cursor-nwse-resize"
              onPointerDown={(e) => handleDragPointerDown(e, 'corner')}
              title="Resize"
            />
          )}
        </>
      )}
    </div>
  )
}
