// src/components/timeline/StemPieces.tsx
// The music & effects or the original voices in pieces (0.3.2), drawn over
// the stem's waveform. Pieces are locked to the picture: they can be cut
// (blade, S, ⌘B), trimmed — a trimmed edge can be dragged back out up to the
// next piece — deleted (silence there), given a volume line and fades; never
// dragged sideways. An original-voices piece can be switched into the export
// (gold, with a star). Every edit sends the stem's whole list (one undo step).
import { useState } from 'react'
import { Star } from 'lucide-react'
import type { Stem, StemPiece } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { cn, pixelsToTime, timeToPixels } from '@/lib/utils'
import { clampFades, fractionToVolume, volumeToFraction } from '@/lib/clipRules'
import { newPieceId, splitPiece, trimPiece } from '@/lib/stemPieces'

// Pixels of drag across the whole volume range: 2 px per dB, as on voice clips
const VOLUME_DRAG_PX = 60

interface StemPiecesProps {
  stem: Stem
  pieces: StemPiece[]
  duration: number
  zoom: number
  pxPerSec: number
  activeTool: 'select' | 'blade'
  /** The row's own colour (music sky, original voices emerald). */
  color: string
  selectedId: string | null
  onSelect: (id: string) => void
  /** A new list for the stem: one PUT + one undo step. */
  onCommit: (pieces: StemPiece[], label: string) => void
  onContextMenu: (piece: StemPiece, x: number, y: number, at: number) => void
}

export function StemPieces({
  stem, pieces, duration, zoom, pxPerSec, activeTool, color, selectedId, onSelect, onCommit, onContextMenu,
}: StemPiecesProps) {
  // The list while an edge, a fade or a volume line is being dragged
  const [draft, setDraft] = useState<StemPiece[] | null>(null)
  const shown = draft ?? pieces
  const px = (t: number) => timeToPixels(t, zoom, pxPerSec)
  const sorted = [...shown].sort((a, b) => a.start_time - b.start_time)

  // The gaps (deleted sound) are dimmed
  const gaps: [number, number][] = []
  let at = 0
  for (const p of sorted) {
    if (p.start_time > at + 0.001) gaps.push([at, p.start_time])
    at = Math.max(at, p.end_time)
  }
  if (duration > at + 0.001) gaps.push([at, duration])

  // A drag that ends with a new list (or none): one commit on release
  const drag = (e: React.PointerEvent, move: (dx: number, dy: number) => StemPiece[] | null, label: string) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    const el = e.currentTarget as HTMLElement
    el.setPointerCapture(e.pointerId)
    const startX = e.clientX
    const startY = e.clientY
    let last: StemPiece[] | null = null
    const onMove = (ev: PointerEvent) => {
      last = move(ev.clientX - startX, ev.clientY - startY)
      if (last) setDraft(last)
    }
    const onUp = (ev: PointerEvent) => {
      el.releasePointerCapture(ev.pointerId)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      setDraft(null)
      useEditorStore.getState().setLivePieceVolume(null)
      if (last && JSON.stringify(last) !== JSON.stringify(pieces)) onCommit(last, label)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return (
    <div className="absolute inset-0 z-10">
      {gaps.map(([a, b]) => (
        <div
          key={`gap-${a}`}
          className="absolute top-0 bottom-0 bg-ink-950/85 pointer-events-none"
          style={{ left: px(a), width: Math.max(1, px(b - a)) }}
        />
      ))}
      {sorted.map((p) => {
        const left = px(p.start_time)
        const width = Math.max(2, px(p.end_time - p.start_time))
        const selected = p.id === selectedId
        const gold = stem === 'voices' && p.in_export
        const fadeInPx = px(p.fade_in_secs)
        const fadeOutPx = px(p.fade_out_secs)
        return (
          <div
            key={p.id}
            data-piece-id={p.id}
            className={cn(
              'absolute top-0 bottom-0 rounded border',
              activeTool === 'blade' ? 'cursor-crosshair' : 'cursor-pointer',
              gold ? 'border-amber-400 bg-amber-400/25' : 'bg-transparent',
              selected && 'outline outline-2 outline-brand-500 -outline-offset-2',
            )}
            style={{ left, width, borderColor: gold ? undefined : `${color}66` }}
            onMouseDown={(e) => e.stopPropagation()}
            onPointerDown={(e) => {
              if (e.button !== 0) return
              e.stopPropagation()
              const t = p.start_time + pixelsToTime(e.clientX - e.currentTarget.getBoundingClientRect().left, zoom, pxPerSec)
              if (activeTool === 'blade') {
                const next = splitPiece(pieces, p.id, t, newPieceId())
                if (next !== pieces) onCommit(next, 'Split')
                return
              }
              onSelect(p.id)
            }}
            onContextMenu={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onSelect(p.id)
              const t = p.start_time + pixelsToTime(e.clientX - e.currentTarget.getBoundingClientRect().left, zoom, pxPerSec)
              onContextMenu(p, e.clientX, e.clientY, t)
            }}
            title={stem === 'music' ? 'Music & effects' : gold ? 'Original voice — in the export' : 'Original voice'}
          >
            {gold && <Star size={11} className="absolute top-1 left-1.5 text-amber-300 fill-amber-300 pointer-events-none" />}

            {/* Fades: shaded slopes */}
            {(p.fade_in_secs > 0 || p.fade_out_secs > 0) && (
              <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox={`0 0 ${width} 100`} preserveAspectRatio="none">
                {p.fade_in_secs > 0 && <polygon points={`0,0 ${fadeInPx},0 0,100`} fill="rgba(0,0,0,0.4)" />}
                {p.fade_out_secs > 0 && <polygon points={`${width},0 ${width - fadeOutPx},0 ${width},100`} fill="rgba(0,0,0,0.4)" />}
              </svg>
            )}

            {activeTool === 'select' && (
              <>
                {/* Edges: cut sound, or bring it back up to the next piece */}
                <div
                  className="absolute left-0 top-0 bottom-0 w-2 cursor-col-resize hover:bg-white/15"
                  onPointerDown={(e) => drag(e, (dx) => trimPiece(pieces, p.id, 'start', p.start_time + pixelsToTime(dx, zoom, pxPerSec), duration), 'Trim start')}
                />
                <div
                  className="absolute right-0 top-0 bottom-0 w-2 cursor-col-resize hover:bg-white/15"
                  onPointerDown={(e) => drag(e, (dx) => trimPiece(pieces, p.id, 'end', p.end_time + pixelsToTime(dx, zoom, pxPerSec), duration), 'Trim end')}
                />
                {/* The volume line */}
                {width >= 40 && (
                  <div
                    className="absolute left-3 right-3 h-[9px] cursor-ns-resize group/vol"
                    style={{ top: `calc(${(1 - volumeToFraction(p.volume_db)) * 100}% - 4px)` }}
                    title="Volume"
                    onPointerDown={(e) => drag(e, (_dx, dy) => {
                      const db = fractionToVolume(volumeToFraction(p.volume_db) - dy / VOLUME_DRAG_PX)
                      useEditorStore.getState().setLivePieceVolume({ id: p.id, db })
                      return pieces.map((x) => (x.id === p.id ? { ...x, volume_db: db } : x))
                    }, 'Change volume')}
                  >
                    <div className="absolute inset-x-0 top-1/2 h-px bg-white/45 group-hover/vol:bg-white/90" />
                    {draft && pieces.find((x) => x.id === p.id)?.volume_db !== p.volume_db && (
                      <span className="absolute -top-4 left-1/2 -translate-x-1/2 rounded bg-ink-900 px-1 text-[10px] font-semibold text-white tabular-nums">
                        {`${p.volume_db > 0 ? '+' : p.volume_db < 0 ? '−' : ''}${Math.abs(p.volume_db)} dB`}
                      </span>
                    )}
                  </div>
                )}
                {/* Fade handles on the selected piece's top corners */}
                {selected && width > 36 && (
                  <>
                    <div
                      className="absolute top-0 h-3 w-3 -translate-x-1/2 rounded-full border border-white/80 bg-ink-900 cursor-ew-resize hover:bg-brand-500"
                      style={{ left: Math.max(6, fadeInPx) }}
                      title="Fade in"
                      onPointerDown={(e) => drag(e, (dx) => {
                        const f = clampFades(p, p.fade_in_secs + pixelsToTime(dx, zoom, pxPerSec), p.fade_out_secs)
                        return pieces.map((x) => (x.id === p.id ? { ...x, ...f } : x))
                      }, 'Fade in')}
                    />
                    <div
                      className="absolute top-0 h-3 w-3 translate-x-1/2 rounded-full border border-white/80 bg-ink-900 cursor-ew-resize hover:bg-brand-500"
                      style={{ right: Math.max(6, fadeOutPx) }}
                      title="Fade out"
                      onPointerDown={(e) => drag(e, (dx) => {
                        const f = clampFades(p, p.fade_in_secs, p.fade_out_secs - pixelsToTime(dx, zoom, pxPerSec))
                        return pieces.map((x) => (x.id === p.id ? { ...x, ...f } : x))
                      }, 'Fade out')}
                    />
                  </>
                )}
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}
