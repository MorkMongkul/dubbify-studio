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
import { audibleAt, fadeGainAt, isVoiced, voiceUrl } from '@/lib/clipRules'
import { useEditorStore } from '@/store/editorStore'

/** Seconds a playing voice may drift from where it should be before it is re-seeked. */
const MAX_DRIFT = 0.1
/** Clips starting this soon are loaded ahead, so they start on time. */
const PRELOAD_AHEAD = 3

interface Player {
  audio: HTMLAudioElement
  url: string
  /** Its gain in the shared AudioContext — can go above the file's own level (the volume line). */
  gain: GainNode | null
}

// One AudioContext for the voices: a gain node per voice carries the volume,
// the fades and a volume line being dragged (an element's own volume stops at 1).
let voiceContext: AudioContext | null = null
function voiceAudioContext(): AudioContext | null {
  if (voiceContext) return voiceContext
  const w = window as Window & typeof globalThis & { webkitAudioContext?: typeof AudioContext }
  const Ctx = w.AudioContext || w.webkitAudioContext
  if (!Ctx) return null
  try { voiceContext = new Ctx() } catch { return null }
  return voiceContext
}

function newPlayer(url: string): Player {
  const audio = newAudio(url)
  const ctx = voiceAudioContext()
  if (!ctx) return { audio, url, gain: null }
  try {
    const gain = ctx.createGain()
    ctx.createMediaElementSource(audio).connect(gain)
    gain.connect(ctx.destination)
    return { audio, url, gain }
  } catch {
    return { audio, url, gain: null }
  }
}

/**
 * A media element routed through the voices' AudioContext, so it can be heard
 * above its file's own level (the music and original voices pieces' volume
 * lines). Once per element; null when Web Audio isn't available.
 */
const routed = new WeakMap<HTMLMediaElement, GainNode | null>()
export function gainFor(el: HTMLMediaElement): GainNode | null {
  if (routed.has(el)) return routed.get(el)!
  const ctx = voiceAudioContext()
  let gain: GainNode | null = null
  if (ctx) {
    try {
      gain = ctx.createGain()
      ctx.createMediaElementSource(el).connect(gain)
      gain.connect(ctx.destination)
    } catch {
      gain = null
    }
  }
  routed.set(el, gain)
  return gain
}

/** Wake the AudioContext once playback starts (browsers start it suspended). */
export function resumeAudio(): void {
  if (voiceContext?.state === 'suspended') voiceContext.resume().catch(() => {})
}

/** Set what a voice is heard at: the editor volume × its fade × a volume line being dragged. */
function setLevel(p: Player, level: number) {
  if (p.gain) {
    if (p.audio.volume !== 1) p.audio.volume = 1
    p.gain.gain.value = level
  } else {
    p.audio.volume = Math.max(0, Math.min(1, level))
  }
}

/** A volume line being dragged plays at its new level before it is saved. */
function liveFactor(seg: Segment): number {
  const live = useEditorStore.getState().liveClipVolume
  return live && live.id === seg.id ? 10 ** ((live.db - (seg.volume_db ?? 0)) / 20) : 1
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
    if (playing && voiceContext?.state === 'suspended') voiceContext.resume().catch(() => {})
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
        p = newPlayer(url)
        map.set(seg.id, p)
      }
      const audio = p.audio
      setLevel(p, liveRef.current.volume * fadeGainAt(seg, t) * liveFactor(seg))
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
      const ready = newPlayer(url)
      ready.audio.currentTime = seg.voice_offset ?? 0
      map.set(seg.id, ready)
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

/** Play one clip's window once — the part of its voice the clip plays, at its rate, with its fades. */
export function previewClip(seg: Segment, volume = 1): void {
  stopPreview()
  if (!isVoiced(seg)) return
  const rate = seg.fit_rate || 1
  const from = seg.voice_offset ?? 0
  const until = from + (seg.end_time - seg.start_time) * rate
  const audio = newAudio(voiceUrl(seg))
  audio.volume = Math.max(0, Math.min(1, volume * fadeGainAt(seg, seg.start_time)))
  audio.playbackRate = clampRate(rate)
  const current = { audio, raf: 0 }
  preview = current
  const watch = () => {
    if (preview !== current) return
    if (audio.currentTime >= until || audio.ended) { stopPreview(); return }
    // Where the clip is on the timeline now, for its fades
    audio.volume = Math.max(0, Math.min(1, volume * fadeGainAt(seg, seg.start_time + (audio.currentTime - from) / rate)))
    current.raf = requestAnimationFrame(watch)
  }
  audio.currentTime = from
  audio.play().then(() => { current.raf = requestAnimationFrame(watch) }).catch(() => {})
}
