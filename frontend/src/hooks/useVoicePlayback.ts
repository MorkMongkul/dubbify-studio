// src/hooks/useVoicePlayback.ts
// The preview plays exactly what the export plays (lib/clipRules.ts, the same
// rule as backend/app/services/timeline.py): every clip under the playhead —
// overlapping clips together — plays its voice file from its cut-off start, at
// its rate, for exactly the clip's length. No speed-up of its own: a voice only
// sounds faster when its clip says so.
//
// Driven by a requestAnimationFrame loop while the video plays (timeupdate fires
// only ~4×/s, too coarse to start a clip on its first word), and reconciled at
// once on pause, seek, a rate or volume change.

import { useCallback, useEffect, useRef, type RefObject } from 'react'
import type { Segment } from '@/types'
import { audibleAt, isVoiced } from '@/lib/clipRules'

/** Seconds a playing voice may drift from where it should be before it is re-seeked. */
const MAX_DRIFT = 0.1
/** Clips starting this soon are loaded ahead, so they start on time. */
const PRELOAD_AHEAD = 3

interface Player {
  audio: HTMLAudioElement
  url: string
}

const voiceUrl = (seg: Segment) => {
  // Cache-bust: the backend overwrites the same file on every re-synthesis
  const versioned = `${seg.tts_audio_url}?v=${seg.tts_duration_secs ?? 0}`
  return versioned.startsWith('/') ? versioned : `/${versioned}`
}

function newAudio(url: string): HTMLAudioElement {
  const audio = new Audio(url)
  audio.preload = 'auto'
  // A rate other than 1 is a time-stretch, like the export's atempo
  ;(audio as HTMLAudioElement & { preservesPitch?: boolean }).preservesPitch = true
  return audio
}

const clampRate = (r: number) => Math.max(0.0625, Math.min(16, r))

interface Options {
  videoRef: RefObject<HTMLVideoElement | null>
  segmentsRef: RefObject<Segment[]>
  isPlaying: boolean
  playbackRate: number
  volume: number
  mutedRef: RefObject<Record<string, boolean>>
  /** The video's source: a new one is a new element to listen to. */
  videoUrl?: string
}

export function useVoicePlayback({ videoRef, segmentsRef, isPlaying, playbackRate, volume, mutedRef, videoUrl }: Options): void {
  const players = useRef(new Map<string, Player>())
  const liveRef = useRef({ isPlaying, volume })
  useEffect(() => { liveRef.current = { isPlaying, volume } }, [isPlaying, volume])

  const reconcile = useCallback(() => {
    const video = videoRef.current
    const map = players.current
    if (!video) {
      for (const p of map.values()) p.audio.pause()
      return
    }
    const t = video.currentTime
    const playing = liveRef.current.isPlaying && !video.paused
    const muted = mutedRef.current ?? {}
    const segs = (segmentsRef.current ?? []).filter((s) => !muted[s.speaker_id ?? '__none__'])
    const wanted = new Map(audibleAt(segs, t).map((h) => [h.seg.id, h]))
    const ahead = segs.filter((s) => isVoiced(s) && s.start_time > t && s.start_time <= t + PRELOAD_AHEAD)

    for (const [id, p] of map) {
      if (wanted.has(id)) continue
      p.audio.pause()
      if (!ahead.some((s) => s.id === id)) map.delete(id)
    }

    for (const { seg, filePos, rate } of wanted.values()) {
      const url = voiceUrl(seg)
      let p = map.get(seg.id)
      if (!p || p.url !== url) {
        p?.audio.pause()
        p = { audio: newAudio(url), url }
        map.set(seg.id, p)
      }
      const audio = p.audio
      audio.volume = liveRef.current.volume
      const wantedRate = clampRate(rate * (video.playbackRate || 1))
      if (Math.abs(audio.playbackRate - wantedRate) > 1e-3) audio.playbackRate = wantedRate
      if (!playing) {
        if (!audio.paused) audio.pause()
        if (Math.abs(audio.currentTime - filePos) > 0.02) audio.currentTime = filePos
        continue
      }
      if (audio.paused) {
        audio.currentTime = filePos
        audio.play().catch(() => {})
      } else if (Math.abs(audio.currentTime - filePos) > MAX_DRIFT) {
        audio.currentTime = filePos
      }
    }

    // Load the next clips ahead, parked on their first kept sound
    for (const seg of ahead) {
      const url = voiceUrl(seg)
      const p = map.get(seg.id)
      if (p && p.url === url) continue
      const audio = newAudio(url)
      audio.currentTime = seg.voice_offset ?? 0
      map.set(seg.id, { audio, url })
    }
  }, [videoRef, segmentsRef, mutedRef])

  // Every frame while playing; once when it stops
  useEffect(() => {
    if (!isPlaying) { reconcile(); return }
    let raf = requestAnimationFrame(function tick() {
      reconcile()
      raf = requestAnimationFrame(tick)
    })
    return () => cancelAnimationFrame(raf)
  }, [isPlaying, reconcile])

  // A rate or volume change applies at once
  useEffect(() => { reconcile() }, [playbackRate, volume, reconcile])

  // Seeks and the video's own play/pause (e.g. buffering, the end)
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const events = ['seeked', 'pause', 'play', 'ratechange', 'ended'] as const
    for (const ev of events) video.addEventListener(ev, reconcile)
    return () => { for (const ev of events) video.removeEventListener(ev, reconcile) }
  }, [videoRef, videoUrl, reconcile])

  // Leaving the editor: silence everything
  useEffect(() => {
    const map = players.current
    return () => {
      for (const p of map.values()) { p.audio.pause(); p.audio.src = '' }
      map.clear()
    }
  }, [])
}

// ── One clip on its own (double-click on the timeline) ────────────────────────

let preview: { audio: HTMLAudioElement; raf: number } | null = null

/** Stop a clip started with previewClip. */
export function stopPreview(): void {
  if (!preview) return
  cancelAnimationFrame(preview.raf)
  preview.audio.pause()
  preview = null
}

/** Play one clip's window once — the part of its voice the clip plays, at its rate. */
export function previewClip(seg: Segment, volume = 1): void {
  stopPreview()
  if (!isVoiced(seg)) return
  const rate = seg.fit_rate || 1
  const from = seg.voice_offset ?? 0
  const until = from + (seg.end_time - seg.start_time) * rate
  const audio = newAudio(voiceUrl(seg))
  audio.volume = volume
  audio.playbackRate = clampRate(rate)
  const current = { audio, raf: 0 }
  preview = current
  const watch = () => {
    if (preview !== current) return
    if (audio.currentTime >= until || audio.ended) { stopPreview(); return }
    current.raf = requestAnimationFrame(watch)
  }
  audio.currentTime = from
  audio.play().then(() => { current.raf = requestAnimationFrame(watch) }).catch(() => {})
}
