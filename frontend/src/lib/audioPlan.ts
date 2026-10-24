// src/lib/audioPlan.ts
// The preview's sound as an editor plays it: every voice and both stems are
// decoded into memory and started on one audio clock (hooks/usePreviewAudio)
// from where the picture is. Media elements kept in step by seeking cut the
// sound in WebKit — Safari and the desktop app — which stalls a playing
// element ~0.6 s on every seek (measured 2026-10-24 on a real episode). A clip
// plays what the export plays (lib/clipRules): its voice from `voice_offset`
// for its length at `fit_rate`; a speed-up is made by the backend with the
// export's own atempo, so the pitch is kept and the preview sounds like the
// export. Pure logic, no runtime imports: `npm test` runs it.
import type { Segment } from '../types/index.ts'
import { isVoiced, voiceUrl } from './clipRules.ts'

/** Seconds between deciding to start a sound and its start: room to schedule it. */
export const LEAD = 0.05

const round6 = (x: number) => Math.round(x * 1e6) / 1e6

type Planned = Pick<Segment, 'id' | 'start_time' | 'end_time' | 'tts_audio_url' | 'tts_duration_secs' | 'voice_offset'
  | 'fit_rate' | 'volume_db' | 'voice_filter' | 'fade_in_secs' | 'fade_out_secs'>

/** The speed a clip's voice is fetched at: its rate when it speeds up (the export only speeds up), else 1. */
export function stretchOf(seg: Pick<Segment, 'fit_rate'>): number {
  const rate = seg.fit_rate || 1
  return rate > 1.001 ? rate : 1
}

/** Where the preview loads a clip's voice: the file itself, or the file at the clip's speed (backend, cached). */
export function previewVoiceUrl(seg: Planned): string {
  const file = voiceUrl(seg)
  const stretch = stretchOf(seg)
  if (stretch === 1) return file
  const version = file.split('?v=')[1] ?? ''
  return `/api/v1/segments/${seg.id}/voice?rate=${stretch.toFixed(4)}&v=${encodeURIComponent(version)}`
}

/** What a clip plays from media time `t` on: when it starts and where in its loaded voice; null once over. */
export function voiceStart(seg: Planned, t: number): { at: number; offset: number } | null {
  if (!isVoiced(seg) || t >= seg.end_time) return null
  const at = Math.max(t, seg.start_time)
  const filePos = (seg.voice_offset ?? 0) + (at - seg.start_time) * (seg.fit_rate || 1)
  return { at, offset: filePos / stretchOf(seg) }
}

/** The clip's fade envelope from media time `t` on: [media time, gain] points joined by straight lines (the export's afade). */
export function fadePoints(seg: Planned, t: number): [number, number][] {
  const { start_time: start, end_time: end } = seg
  const fadeIn = seg.fade_in_secs ?? 0
  const fadeOut = seg.fade_out_secs ?? 0
  const gainAt = (x: number) => {
    let gain = 1
    if (fadeIn > 0) gain = Math.min(gain, (x - start) / fadeIn)
    if (fadeOut > 0) gain = Math.min(gain, (end - x) / fadeOut)
    return round6(Math.max(0, gain))
  }
  const from = Math.max(t, start)
  const points: [number, number][] = []
  for (const x of [from, start + fadeIn, end - fadeOut, end].map(round6).sort((a, b) => a - b)) {
    if (x < from || x > end || points.some(([p]) => p === x)) continue
    points.push([x, gainAt(x)])
  }
  return points
}

/** Everything that changes what a clip plays (not its live level): a change starts it again. */
export function soundKey(seg: Planned): string {
  return [previewVoiceUrl(seg), seg.start_time, seg.end_time, seg.voice_offset ?? 0,
    seg.fade_in_secs ?? 0, seg.fade_out_secs ?? 0].join('|')
}

/** Voiced clips still playing at `t` or starting within `ahead` seconds. */
export function upcoming<S extends Planned>(segs: S[], t: number, ahead: number): S[] {
  return segs.filter((s) => isVoiced(s) && s.end_time > t && s.start_time < t + ahead)
}

/** Every voiced clip, in the order to load it: from the playhead on (by start), then the ones before it, nearest first. */
export function loadOrder<S extends Planned>(segs: S[], t: number): S[] {
  const voiced = segs.filter((s) => isVoiced(s))
  const ahead = voiced.filter((s) => s.end_time > t).sort((a, b) => a.start_time - b.start_time)
  const before = voiced.filter((s) => s.end_time <= t).sort((a, b) => b.start_time - a.start_time)
  return [...ahead, ...before]
}

/** Media time `media` plays at audio-clock time `ctx`, at `rate` media seconds per second. */
export interface Anchor { ctx: number; media: number; rate: number }

export const toMedia = (a: Anchor, ctx: number) => a.media + (ctx - a.ctx) * a.rate
export const toCtx = (a: Anchor, media: number) => a.ctx + (media - a.media) / a.rate

/**
 * Start the sound where the video is: what is scheduled at clock time c is
 * heard `latency` later, when the video will have moved on that far.
 */
export function anchorAt(ctxNow: number, videoTime: number, rate: number, latency: number): Anchor {
  return { ctx: ctxNow + LEAD, media: videoTime + (LEAD + latency) * rate, rate }
}

/** The media time being heard at clock time `ctxNow`. */
export function heardAt(a: Anchor, ctxNow: number, latency: number): number {
  return toMedia(a, ctxNow - latency)
}

/** WebKit's video starts moving ~20 ms after play() (measured 2026-10-24, every play after the first). */
export const VIDEO_START = 0.02

/**
 * Playing from a still picture: the sound starts at once from the playhead…
 * (anchored on where the video is, its first word isn't skipped — Bluetooth
 * earbuds are heard ~0.4 s after the sound is sent).
 */
export function stillAnchor(ctxNow: number, position: number, rate: number): Anchor {
  return { ctx: ctxNow + LEAD, media: position, rate }
}

/** …and the picture starts once that sound is heard (s to wait before play()). */
export function pictureHold(latency: number): number {
  return Math.max(0, LEAD + latency - VIDEO_START)
}
