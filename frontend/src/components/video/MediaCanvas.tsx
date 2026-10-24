// src/components/video/MediaCanvas.tsx
// The episode's media over the picture (0.4.0). One canvas over the frame
// draws every media clip under the playhead in track order (lib/mediaGl), so
// a transparent video stays transparent and layers cover each other as the
// export does. Each video clip near the playhead has a hidden player of its
// own, kept in step with the picture (lib/media mediaVideoStep): it follows
// the frames the main video shows while playing, and shows the exact frame
// when stopped. The one selected clip gets a box: drag it to move the clip,
// a corner to resize it with its shape kept — one change, one undo step.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { MediaAsset, MediaBox } from '@/types'
import { mediaLockedNotice, useMediaEditing } from '@/hooks/useMediaEditing'
import { pictureStartDelay } from '@/hooks/usePreviewAudio'
import { drawOrder, mediaSwitchesOf, mediaVideoStep, mediaWindow, resizeBox } from '@/lib/media'
import { MediaRenderer, type MediaLayer } from '@/lib/mediaGl'
import { useEditorStore } from '@/store/editorStore'

type Rect = { left: number; top: number; width: number; height: number }
type Corner = 'nw' | 'ne' | 'sw' | 'se'
const CORNERS: Corner[] = ['nw', 'ne', 'sw', 'se']

interface MediaCanvasProps {
  jobId: string
  projectId?: string
  videoRef: React.RefObject<HTMLVideoElement | null>
  /** The player's box: the frame is the video fitted inside it. */
  containerRef: React.RefObject<HTMLDivElement | null>
}

export function MediaCanvas({ jobId, projectId, videoRef, containerRef }: MediaCanvasProps) {
  const duration = useEditorStore((s) => s.duration)
  const isPlaying = useEditorStore((s) => s.isPlaying)
  const playbackRate = useEditorStore((s) => s.playbackRate)
  const videoSize = useEditorStore((s) => s.videoSize)
  const selectedIds = useEditorStore((s) => s.selectedMediaIds)
  const edit = useMediaEditing(jobId, projectId, duration)
  const clips = useMemo(() => edit.list?.clips ?? [], [edit.list])
  const flags = edit.list?.track_flags
  const kinds = useMemo(() => new Map([...edit.assets.values()].map((a) => [a.id, a.kind])), [edit.assets])

  // The frame inside the player (the player has the video's shape, but not in full screen)
  const [frame, setFrame] = useState<Rect | null>(null)
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const measure = () => {
      const r = el.getBoundingClientRect()
      const aspect = videoSize ? videoSize.width / videoSize.height : r.width / Math.max(1, r.height)
      const width = Math.min(r.width, r.height * aspect)
      const height = width / aspect
      const next = { left: (r.width - width) / 2, top: (r.height - height) / 2, width, height }
      setFrame((was) => (was && Math.abs(was.width - next.width) < 0.5 && Math.abs(was.height - next.height) < 0.5
        && Math.abs(was.left - next.left) < 0.5 && Math.abs(was.top - next.top) < 0.5 ? was : next))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [containerRef, videoSize])

  const canvasRef = useRef<HTMLCanvasElement>(null)
  const renderer = useRef<MediaRenderer | null>(null)
  // The players of the video clips near the playhead; re-rendered only when that set changes
  const [windowIds, setWindowIds] = useState<string[]>([])
  const players = useRef(new Map<string, HTMLVideoElement>())
  const images = useRef(new Map<string, HTMLImageElement>())
  // Each player since it was last started: when, and whether it was put back since
  const runs = useRef(new Map<string, { playAt: number; refitted: boolean }>())
  // The picture's start delay in media seconds, read as playing starts
  const lead = useRef(0)
  const pendingSeek = useRef(new Map<string, number>())
  // A box being dragged in the preview: drawn there until it's saved
  const [draft, setDraft] = useState<{ id: string; box: MediaBox } | null>(null)

  // What a frame needs, latest (the frame loop isn't re-created per change)
  const latest = useRef({ clips, flags, assets: edit.assets, kinds, draft, rate: playbackRate })
  useEffect(() => { latest.current = { clips, flags, assets: edit.assets, kinds, draft, rate: playbackRate } })
  const paintRef = useRef<(t: number, playing: boolean) => void>(() => {})
  const windowKey = useRef('')

  useEffect(() => {
    const seek = (v: HTMLVideoElement, id: string, to: number) => {
      // A seek in flight takes the newest target when it lands (scrubbing)
      if (v.seeking) pendingSeek.current.set(id, to)
      else v.currentTime = to
    }
    const imageFor = (a: MediaAsset) => {
      let img = images.current.get(a.id)
      if (!img && a.url) {
        img = new Image()
        img.decoding = 'async'
        img.onload = () => paintRef.current(useEditorStore.getState().currentTime, false)
        img.src = a.url
        images.current.set(a.id, img)
      }
      return img
    }
    paintRef.current = (t, playing) => {
      const { clips: list, flags: switches, assets, kinds: kindOf, draft: dragged, rate } = latest.current
      const need = mediaWindow(list, kindOf, t)
      if (need.join('|') !== windowKey.current) {
        windowKey.current = need.join('|')
        setWindowIds(need)
      }
      const now = performance.now()
      for (const c of list) {
        const v = players.current.get(c.id)
        if (!v || v.readyState < 1) continue
        const run = runs.current.get(c.id)
        const step = mediaVideoStep(c, t, playing, v, {
          lead: playing ? lead.current : 0, sincePlayMs: now - (run?.playAt ?? 0), refitted: run?.refitted ?? false,
        })
        if (step.kind === 'play') {
          v.playbackRate = rate       // only while stopped: a speed change stalls a playing video
          if (step.seek != null) v.currentTime = step.seek
          v.play().catch(() => {})
          runs.current.set(c.id, { playAt: now, refitted: false })
        } else if (step.kind === 'refit') {
          v.pause()
          v.currentTime = step.seek
          v.play().catch(() => {})
          runs.current.set(c.id, { playAt: now, refitted: true })
        } else if (step.kind === 'pause') {
          if (!v.paused) v.pause()
          if (step.seek != null) seek(v, c.id, step.seek)
        }
      }
      const layers: MediaLayer[] = []
      for (const c of drawOrder(list, switches)) {
        if (t < c.start_time || t >= c.end_time) continue
        const a = assets.get(c.asset_id)
        if (!a || a.status !== 'ready') continue
        const box = dragged?.id === c.id ? dragged.box : c
        if (a.kind === 'video') {
          const v = players.current.get(c.id)
          if (v) layers.push({ source: v, key: c.id, stacked: a.has_alpha && a.alpha_kept, box })
        } else {
          const img = imageFor(a)
          if (img) layers.push({ source: img, key: a.id, stacked: false, box })
        }
      }
      renderer.current?.draw(layers)
    }
  }, [])

  // The canvas: as many pixels as the frame shows
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !frame) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.max(1, Math.round(frame.width * dpr))
    canvas.height = Math.max(1, Math.round(frame.height * dpr))
    if (!renderer.current) {
      try { renderer.current = new MediaRenderer(canvas) } catch { return }
    }
    paintRef.current(useEditorStore.getState().currentTime, false)
  }, [frame])
  useEffect(() => () => { renderer.current?.dispose(); renderer.current = null }, [])

  // Playing: every frame the main video shows; stopped: wherever the playhead goes
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (!isPlaying) {
      paintRef.current(useEditorStore.getState().currentTime, false)
      return useEditorStore.subscribe((s) => s.currentTime, (t) => paintRef.current(t, false))
    }
    lead.current = pictureStartDelay() * (useEditorStore.getState().playbackRate || 1)
    // The picture paused while playing (the preview engine holds it to start again in step): the players wait too
    const onPause = () => paintRef.current(video.currentTime, false)
    video.addEventListener('pause', onPause)
    let handle = 0
    let stopped = false
    if (typeof video.requestVideoFrameCallback === 'function') {
      const onFrame = (_now: number, meta: VideoFrameCallbackMetadata) => {
        if (stopped) return
        paintRef.current(meta.mediaTime, !video.paused)
        handle = video.requestVideoFrameCallback(onFrame)
      }
      handle = video.requestVideoFrameCallback(onFrame)
      return () => { stopped = true; video.cancelVideoFrameCallback(handle); video.removeEventListener('pause', onPause) }
    }
    const onTick = () => {
      if (stopped) return
      paintRef.current(video.currentTime, !video.paused)
      handle = requestAnimationFrame(onTick)
    }
    handle = requestAnimationFrame(onTick)
    return () => { stopped = true; cancelAnimationFrame(handle); video.removeEventListener('pause', onPause) }
  }, [isPlaying, videoRef])

  // A change to the clips, their media or a box being dragged shows at once while stopped
  useEffect(() => {
    if (!useEditorStore.getState().isPlaying) paintRef.current(useEditorStore.getState().currentTime, false)
  }, [clips, flags, edit.assets, draft])

  // A player's frame arrived (a seek landed, or it loaded): draw it while stopped
  const onPlayerFrame = (id: string) => {
    const v = players.current.get(id)
    const next = pendingSeek.current.get(id)
    if (v && next != null) {
      pendingSeek.current.delete(id)
      v.currentTime = next
      return
    }
    if (!useEditorStore.getState().isPlaying) paintRef.current(useEditorStore.getState().currentTime, false)
  }

  // The selection box: one selected clip, under the playhead, on a shown track
  const selected = selectedIds.length === 1 ? clips.find((c) => c.id === selectedIds[0]) : undefined
  const selectedShown = useEditorStore((s) => !!selected && s.currentTime >= selected.start_time && s.currentTime < selected.end_time)
  const showBox = !!selected && selectedShown && !mediaSwitchesOf(flags, selected.lane_index).hidden && !!frame
  const box = selected && (draft?.id === selected.id ? draft.box : selected)

  const dragBox = (e: React.PointerEvent, corner: Corner | null) => {
    if (e.button !== 0 || !selected || !frame) return
    e.stopPropagation()
    e.preventDefault()
    if (edit.isLocked(selected)) { mediaLockedNotice(); return }
    const el = e.currentTarget as HTMLElement
    el.setPointerCapture(e.pointerId)
    const x0 = e.clientX
    const y0 = e.clientY
    const from: MediaBox = { x: selected.x, y: selected.y, width: selected.width, height: selected.height }
    let next = from
    const onMove = (ev: PointerEvent) => {
      const dx = (ev.clientX - x0) / frame.width
      const dy = (ev.clientY - y0) / frame.height
      next = corner ? resizeBox(from, corner, dx) : { ...from, x: from.x + dx, y: from.y + dy }
      setDraft({ id: selected.id, box: next })
    }
    const onUp = (ev: PointerEvent) => {
      if (el.hasPointerCapture(ev.pointerId)) el.releasePointerCapture(ev.pointerId)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      setDraft(null)
      if (next !== from) {
        edit.commit(clips.map((c) => (c.id === selected.id ? { ...c, ...next } : c)), corner ? 'Resize media' : 'Move media')
      }
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  return (
    <>
      {/* The players: laid out at the frame's size (WebKit doesn't advance a video it thinks
          is off screen) but not seen — their frames are drawn on the canvas */}
      {frame && windowIds.map((id) => {
        const c = clips.find((x) => x.id === id)
        const a = c && edit.assets.get(c.asset_id)
        if (!a?.preview_url) return null
        return (
          <video
            key={id}
            ref={(el) => { if (el) players.current.set(id, el); else players.current.delete(id) }}
            src={a.preview_url}
            muted
            playsInline
            preload="auto"
            className="absolute pointer-events-none"
            style={{ ...frame, opacity: 0 }}
            onSeeked={() => onPlayerFrame(id)}
            onLoadedData={() => onPlayerFrame(id)}
          />
        )
      })}
      <canvas
        ref={canvasRef}
        className="absolute pointer-events-none"
        style={frame ? { ...frame } : { display: 'none' }}
      />
      {showBox && box && frame && (
        <div
          className="absolute z-30 border-2 border-white/90 shadow-[0_0_0_1px_rgba(0,0,0,0.5)] cursor-move"
          style={{
            left: frame.left + box.x * frame.width,
            top: frame.top + box.y * frame.height,
            width: box.width * frame.width,
            height: box.height * frame.height,
          }}
          aria-label="Move the media"
          onPointerDown={(e) => dragBox(e, null)}
          onClick={(e) => e.stopPropagation()}
        >
          {CORNERS.map((corner) => (
            <div
              key={corner}
              aria-label="Resize the media"
              className={`absolute h-3 w-3 rounded-full border-2 border-white bg-brand-500 ${
                corner === 'nw' ? '-left-1.5 -top-1.5 cursor-nwse-resize'
                  : corner === 'ne' ? '-right-1.5 -top-1.5 cursor-nesw-resize'
                    : corner === 'sw' ? '-left-1.5 -bottom-1.5 cursor-nesw-resize'
                      : '-right-1.5 -bottom-1.5 cursor-nwse-resize'}`}
              onPointerDown={(e) => dragBox(e, corner)}
            />
          ))}
        </div>
      )}
    </>
  )
}
