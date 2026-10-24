// src/components/timeline/MediaTracks.tsx
// The episode's media tracks (0.4.0), above the picture strip like CapCut's
// overlay tracks. The top track is drawn over the ones under it, so a clip
// never changes track by itself: it is dragged in time and between media
// tracks (lib/media planMediaDrop — a busy or locked track, or a drop between
// tracks, makes a new track), trimmed (a video never past its own end),
// split, copied, duplicated and deleted (hooks/useMediaEditing), and a tile
// from the Media tab drops where the pointer is. Each header: the track's
// kind, lock, hide and — when a clip has sound — mute.
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Clapperboard, ClipboardPaste, Copy, CopyPlus, Eye, EyeOff, Image as ImageIcon, Lock, LockOpen,
  SplitSquareHorizontal, Trash2, Volume2, VolumeX,
} from 'lucide-react'
import type { MediaClip } from '@/types'
import type { MediaEditing } from '@/hooks/useMediaEditing'
import { mediaLockedNotice } from '@/hooks/useMediaEditing'
import { MEDIA_DRAG, mediaRows, mediaSwitchesOf, planMediaDrop, trimMediaEnd, trimMediaStart } from '@/lib/media'
import { dropSpot, type DropSpot } from '@/lib/tracks'
import { volumeToFraction } from '@/lib/clipRules'
import { dbLabel, volumeAfterDrag } from '@/lib/waveform'
import { cn, pixelsToTime, timeToPixels } from '@/lib/utils'
import { useEditorStore } from '@/store/editorStore'
import { ContextMenu, type MenuItem } from '@/components/ui/ContextMenu'
import { Tooltip } from '@/components/ui/Tooltip'
import { TrackSwitch } from './TrackSwitch'
import { DragTip } from './DragTip'

export const MEDIA_LANE_HEIGHT = 44
// A checkerboard under each clip's pictures, so transparency reads as transparency
const CHECKER = 'conic-gradient(#262b40 25%, #1c2033 0 50%, #262b40 0 75%, #1c2033 0)'

const samePlaces = (a: MediaClip[], b: MediaClip[]) =>
  a.length === b.length && a.every((c, i) =>
    c.id === b[i].id && c.lane_index === b[i].lane_index && c.start_time === b[i].start_time && c.end_time === b[i].end_time)

interface MediaTracksProps {
  edit: MediaEditing
  zoom: number
  pxPerSec: number
  duration: number
  activeTool: 'select' | 'blade'
  speakerWidth: number
  /** The timeline's scrolling content: a tile dragged anywhere over it drops onto the media tracks. */
  contentRef: React.RefObject<HTMLDivElement | null>
}

export function MediaTracks({ edit, zoom, pxPerSec, duration, activeTool, speakerWidth, contentRef }: MediaTracksProps) {
  const clips = useMemo(() => edit.list?.clips ?? [], [edit.list])
  const flags = edit.list?.track_flags
  const rows = useMemo(() => mediaRows(clips), [clips])
  const selectedIds = useEditorStore((s) => s.selectedMediaIds)
  const blockRef = useRef<HTMLDivElement>(null)
  const lineRef = useRef<HTMLDivElement>(null)
  // A tile from the Media tab is being dragged over the timeline
  const [tileOver, setTileOver] = useState(false)
  // Clips being dragged follow the pointer; an edge or a volume line shows its result
  const [moving, setMoving] = useState<{ ids: string[]; dx: number; dy: number } | null>(null)
  const [draft, setDraft] = useState<MediaClip | null>(null)
  const [volumeTip, setVolumeTip] = useState<{ x: number; y: number; db: number } | null>(null)
  const [menu, setMenu] = useState<{ clip: MediaClip; x: number; y: number; at: number } | null>(null)
  const px = (t: number) => timeToPixels(t, zoom, pxPerSec)

  // Where a pointer at clientY would drop: onto a row, or a new track between rows
  const spotAt = (clientY: number): DropSpot => {
    const top = blockRef.current?.getBoundingClientRect().top ?? 0
    return rows.length ? dropSpot(clientY - top, rows.length, MEDIA_LANE_HEIGHT) : { kind: 'insert', boundary: 0 }
  }
  const showSpot = (spot: DropSpot | null) => {
    const line = lineRef.current
    if (!line) return
    line.style.display = spot?.kind === 'insert' && rows.length ? 'block' : 'none'
    if (spot?.kind === 'insert') line.style.top = `${spot.boundary * MEDIA_LANE_HEIGHT - 1}px`
  }

  // A tile dropped anywhere on the timeline lands on the media tracks, at the pointer's time
  const dropRef = useRef({ spotAt, showSpot, edit, zoom, pxPerSec, speakerWidth })
  useEffect(() => { dropRef.current = { spotAt, showSpot, edit, zoom, pxPerSec, speakerWidth } })
  useEffect(() => {
    const el = contentRef.current
    if (!el) return
    const isTile = (e: DragEvent) => !!e.dataTransfer && Array.from(e.dataTransfer.types).includes(MEDIA_DRAG)
    // WebKit gives dragleave no relatedTarget: leaving waits a moment for the next dragover
    let leaving: number | undefined
    const clear = () => { setTileOver(false); dropRef.current.showSpot(null) }
    const over = (e: DragEvent) => {
      if (!isTile(e)) return
      e.preventDefault()
      e.dataTransfer!.dropEffect = 'copy'
      window.clearTimeout(leaving)
      setTileOver(true)
      dropRef.current.showSpot(dropRef.current.spotAt(e.clientY))
    }
    const leave = () => { window.clearTimeout(leaving); leaving = window.setTimeout(clear, 120) }
    const drop = (e: DragEvent) => {
      if (!isTile(e)) return
      e.preventDefault()
      e.stopPropagation()
      window.clearTimeout(leaving)
      clear()
      const { edit: ed, zoom: z, pxPerSec: pps, speakerWidth: sw, spotAt: at } = dropRef.current
      const asset = ed.assets.get(e.dataTransfer!.getData(MEDIA_DRAG))
      if (!asset) return
      const t = Math.max(0, pixelsToTime(e.clientX - el.getBoundingClientRect().left - sw, z, pps))
      ed.dropTile(asset, t, at(e.clientY))
    }
    el.addEventListener('dragover', over)
    el.addEventListener('dragleave', leave)
    el.addEventListener('drop', drop)
    window.addEventListener('dragend', clear)
    return () => {
      window.clearTimeout(leaving)
      el.removeEventListener('dragover', over)
      el.removeEventListener('dragleave', leave)
      el.removeEventListener('drop', drop)
      window.removeEventListener('dragend', clear)
    }
  }, [contentRef])

  // A pointer drag: `move` on each step, `end` on release (save) or cancel
  const track = (e: React.PointerEvent, move: (dx: number, dy: number, ev: PointerEvent) => void, end: (save: boolean, ev: PointerEvent) => void) => {
    const el = e.currentTarget as HTMLElement
    el.setPointerCapture(e.pointerId)
    const x0 = e.clientX
    const y0 = e.clientY
    const onMove = (ev: PointerEvent) => move(ev.clientX - x0, ev.clientY - y0, ev)
    const finish = (save: boolean) => (ev: PointerEvent) => {
      if (el.hasPointerCapture(ev.pointerId)) el.releasePointerCapture(ev.pointerId)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      end(save, ev)
    }
    const onUp = finish(true)
    const onCancel = finish(false)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
  }

  const timeAt = (c: MediaClip, clientX: number, el: HTMLElement) =>
    c.start_time + pixelsToTime(clientX - el.getBoundingClientRect().left, zoom, pxPerSec)

  const onClipDown = (e: React.PointerEvent, c: MediaClip) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    const store = useEditorStore.getState()
    const locked = edit.isLocked(c)
    if (activeTool === 'blade') {
      if (locked) mediaLockedNotice()
      else edit.split(timeAt(c, e.clientX, e.currentTarget as HTMLElement), [c.id])
      return
    }
    // Shift+click adds or removes it; a plain click on a clip outside the selection selects just it
    let ids = store.selectedMediaIds
    if (e.shiftKey) {
      store.setSelectedMedia(ids.includes(c.id) ? ids.filter((x) => x !== c.id) : [...ids, c.id])
      return
    }
    if (!ids.includes(c.id)) {
      ids = [c.id]
      store.setSelectedMedia(ids)
    }
    const group = clips.filter((x) => ids.includes(x.id) && !edit.isLocked(x))
    const first = Math.min(...group.map((x) => x.start_time))
    const last = Math.max(...group.map((x) => x.end_time))
    // The group stays inside the episode
    const clampDelta = (secs: number) => Math.max(-first, duration > 0 ? Math.min(secs, duration - last) : secs)
    let dragging = false
    let delta = 0
    let spot: DropSpot = { kind: 'onto', row: rows.indexOf(c.lane_index) }
    track(e, (dx, dy, ev) => {
      if (!dragging && Math.abs(dx) < 3 && Math.abs(dy) < 3) return
      if (!dragging && locked) { mediaLockedNotice(); dragging = true; return }
      dragging = true
      if (locked) return
      delta = clampDelta(pixelsToTime(dx, zoom, pxPerSec))
      spot = spotAt(ev.clientY)
      setMoving({ ids: group.map((x) => x.id), dx: px(delta), dy })
      showSpot(spot)
    }, (save) => {
      setMoving(null)
      showSpot(null)
      if (!save || !dragging || locked) return
      const planned = planMediaDrop(clips, group.map((x) => x.id), c.id, delta, spot, flags, duration)
      if (!samePlaces(planned.clips, clips)) edit.commit(planned.clips, 'Move', planned.track_flags)
    })
  }

  const onEdgeDown = (e: React.PointerEvent, c: MediaClip, side: 'start' | 'end') => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    const asset = edit.assets.get(c.asset_id)
    if (edit.isLocked(c)) { mediaLockedNotice(); return }
    if (!asset) return
    const others = clips.filter((o) => o.id !== c.id)
    let next = c
    track(e, (dx) => {
      const t = (side === 'start' ? c.start_time : c.end_time) + pixelsToTime(dx, zoom, pxPerSec)
      next = side === 'start'
        ? { ...c, ...trimMediaStart(c, asset, t, others) }
        : { ...c, ...trimMediaEnd(c, asset, t, duration, others) }
      setDraft(next)
    }, (save) => {
      setDraft(null)
      if (save && (next.start_time !== c.start_time || next.end_time !== c.end_time)) {
        edit.commit(clips.map((x) => (x.id === c.id ? next : x)), side === 'start' ? 'Trim start' : 'Trim end')
      }
    })
  }

  // The volume line: 2 px per dB from where it was grabbed (like the voice clips')
  const onVolumeDown = (e: React.PointerEvent, c: MediaClip) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    if (edit.isLocked(c)) { mediaLockedNotice(); return }
    let db = c.volume_db
    track(e, (_dx, dy, ev) => {
      db = volumeAfterDrag(c.volume_db, dy)
      setDraft({ ...c, volume_db: db })
      setVolumeTip({ x: ev.clientX, y: ev.clientY, db })
    }, (save) => {
      setDraft(null)
      setVolumeTip(null)
      if (save && db !== c.volume_db) edit.commit(clips.map((x) => (x.id === c.id ? { ...x, volume_db: db } : x)), 'Change volume')
    })
  }

  const onClipMenu = (e: React.MouseEvent, c: MediaClip) => {
    e.preventDefault()
    e.stopPropagation()
    const store = useEditorStore.getState()
    if (!store.selectedMediaIds.includes(c.id)) store.setSelectedMedia([c.id])
    // The playhead when it's over the clip, else where the clip was clicked
    const t = store.currentTime
    const at = t > c.start_time && t < c.end_time ? t : timeAt(c, e.clientX, e.currentTarget as HTMLElement)
    setMenu({ clip: c, x: e.clientX, y: e.clientY, at })
  }

  const menuItems = (m: { clip: MediaClip; at: number }): MenuItem[] => {
    const locked = edit.isLocked(m.clip)
    return [
      { icon: Copy, label: 'Copy', shortcut: '⌘C', onSelect: () => { edit.copy() } },
      { icon: ClipboardPaste, label: 'Paste', shortcut: '⌘V', disabled: !useEditorStore.getState().mediaClipboard.length, onSelect: () => { edit.paste() } },
      { icon: CopyPlus, label: 'Duplicate', shortcut: '⌘D', disabled: locked, onSelect: edit.duplicate },
      'separator',
      { icon: SplitSquareHorizontal, label: 'Split', shortcut: 'S', disabled: locked, onSelect: () => edit.split(m.at) },
      'separator',
      { icon: Trash2, label: 'Delete', shortcut: '⌫', disabled: locked, onSelect: edit.remove },
    ]
  }

  if (!rows.length && !tileOver) return <div ref={blockRef} />

  return (
    <div ref={blockRef} className="relative">
      {!rows.length && (
        <div className="relative border-b border-timeline-grid" style={{ height: MEDIA_LANE_HEIGHT }}>
          <div
            className="absolute top-1 bottom-1 right-1 rounded-md border border-dashed border-violet-400/60 bg-violet-500/10 flex items-center px-3 text-[11px] text-violet-200/80 pointer-events-none"
            style={{ left: 'calc(var(--speaker-width) + 4px)' }}
          >
            Drop to add it to the episode
          </div>
        </div>
      )}
      {rows.map((lane) => {
        const laneClips = clips.filter((c) => c.lane_index === lane)
        const switches = mediaSwitchesOf(flags, lane)
        const hasVideo = laneClips.some((c) => edit.assets.get(c.asset_id)?.kind === 'video')
        const hasSound = laneClips.some((c) => edit.assets.get(c.asset_id)?.has_audio)
        return (
          <div key={lane} className="relative border-b border-timeline-grid bg-violet-500/[0.03]" style={{ height: MEDIA_LANE_HEIGHT }}>
            <div
              className="sticky left-0 z-[35] h-full shrink-0 flex items-center gap-1.5 px-2.5 bg-ink-900 border-r border-white/[0.06]"
              style={{ float: 'left', width: 'var(--speaker-width)' }}
            >
              <Tooltip content={hasVideo ? 'Video track' : 'Image track'} side="right">
                <span className="h-5 w-5 rounded flex items-center justify-center shrink-0 bg-violet-500/15 text-violet-300">
                  {hasVideo ? <Clapperboard size={12} /> : <ImageIcon size={12} />}
                </span>
              </Tooltip>
              <div className="ml-auto flex items-center gap-1">
                <TrackSwitch on={switches.locked} label={switches.locked ? 'Unlock track' : 'Lock track'}
                  icon={switches.locked ? Lock : LockOpen} onClick={() => edit.toggleSwitch(lane, 'locked')} />
                <TrackSwitch on={switches.hidden} label={switches.hidden ? 'Show track' : 'Hide track'}
                  icon={switches.hidden ? EyeOff : Eye} onClick={() => edit.toggleSwitch(lane, 'hidden')} />
                {hasSound && (
                  <TrackSwitch on={switches.muted} label={switches.muted ? 'Unmute track' : 'Mute track'}
                    icon={switches.muted ? VolumeX : Volume2} onClick={() => edit.toggleSwitch(lane, 'muted')} />
                )}
              </div>
            </div>

            <div className="absolute top-0 bottom-0 right-0" style={{ left: 'var(--speaker-width)' }}>
              {laneClips.map((c) => {
                const shown = draft?.id === c.id ? draft : c
                const asset = edit.assets.get(c.asset_id)
                const left = px(shown.start_time)
                const width = Math.max(4, px(shown.end_time - shown.start_time))
                const selected = selectedIds.includes(c.id)
                const dragged = moving?.ids.includes(c.id)
                return (
                  <div
                    key={c.id}
                    data-media-clip={c.id}
                    className={cn(
                      'absolute top-1 bottom-1 rounded-md border overflow-hidden',
                      selected ? 'border-white ring-2 ring-brand-500' : 'border-violet-300/40',
                      activeTool === 'blade' ? 'cursor-crosshair' : 'cursor-pointer',
                      (switches.hidden || switches.locked) && 'opacity-50',
                      dragged && 'z-30 shadow-xl opacity-90',
                    )}
                    style={{
                      left,
                      width,
                      transform: dragged ? `translate(${moving!.dx}px, ${moving!.dy}px)` : undefined,
                      backgroundImage: asset?.thumb_url ? `url("${asset.thumb_url}"), ${CHECKER}` : CHECKER,
                      backgroundSize: 'auto 100%, 10px 10px',
                      backgroundRepeat: 'repeat-x, repeat',
                    }}
                    title={asset?.name}
                    onMouseDown={(e) => e.stopPropagation()}
                    onPointerDown={(e) => onClipDown(e, c)}
                    onContextMenu={(e) => onClipMenu(e, c)}
                  >
                    <span className="absolute top-0.5 left-1 max-w-[calc(100%-8px)] truncate rounded bg-black/65 px-1 text-[10px] font-medium text-white/90 pointer-events-none">
                      {asset?.name ?? 'Media'}
                    </span>
                    {asset?.has_audio && width >= 40 && activeTool === 'select' && (
                      <div
                        className="absolute left-3 right-3 h-[9px] cursor-ns-resize group/vol"
                        style={{ top: `calc(${(1 - volumeToFraction(shown.volume_db)) * 100}% - 4px)` }}
                        aria-label="Volume"
                        onPointerDown={(e) => onVolumeDown(e, c)}
                        onDoubleClick={(e) => {
                          // Back to 0 dB
                          e.stopPropagation()
                          if (c.volume_db !== 0 && !edit.isLocked(c)) {
                            edit.commit(clips.map((x) => (x.id === c.id ? { ...x, volume_db: 0 } : x)), 'Change volume')
                          }
                        }}
                      >
                        <div className="absolute inset-x-0 top-1/2 h-px bg-white/55 group-hover/vol:bg-white" />
                        <span className="absolute right-0 top-1/2 -translate-y-1/2 rounded bg-ink-900/90 px-1.5 text-[10.5px] font-semibold text-white tabular-nums pointer-events-none hidden group-hover/vol:block">
                          {dbLabel(shown.volume_db)}
                        </span>
                      </div>
                    )}
                    {activeTool === 'select' && !switches.locked && (
                      <>
                        <div className="absolute left-0 top-0 bottom-0 w-2 cursor-col-resize hover:bg-white/25"
                          onPointerDown={(e) => onEdgeDown(e, c, 'start')} />
                        <div className="absolute right-0 top-0 bottom-0 w-2 cursor-col-resize hover:bg-white/25"
                          onPointerDown={(e) => onEdgeDown(e, c, 'end')} />
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
      {/* Where a drop would insert a new media track */}
      <div
        ref={lineRef}
        className="absolute right-0 h-[2px] bg-violet-400 pointer-events-none z-40"
        style={{ left: 'var(--speaker-width)', display: 'none', boxShadow: '0 0 6px rgba(167,139,250,0.8)' }}
      />
      {volumeTip && <DragTip x={volumeTip.x} y={volumeTip.y} text={dbLabel(volumeTip.db)} />}
      {/* The menu is a portal, but React still bubbles its presses up here: they mustn't
          reach the timeline's empty-space press (a seek that clears the selection) */}
      {menu && (
        <div onMouseDown={(e) => e.stopPropagation()}>
          <ContextMenu x={menu.x} y={menu.y} items={menuItems(menu)} onClose={() => setMenu(null)} />
        </div>
      )}
    </div>
  )
}
