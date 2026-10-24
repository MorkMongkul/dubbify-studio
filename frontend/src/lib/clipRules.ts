// The timeline's timing rule, as the editor applies it. Keep in lockstep with
// backend/app/services/timeline.py (tests/clipRules.test.ts reads its constants).
//
// A clip with a voice plays its voice file from `voice_offset` for
// (end_time − start_time) seconds at `fit_rate`, starting at `start_time` — so it
// is exactly as long as the sound it plays. A clip with no voice yet is as long as
// its Khmer text takes to say. Pure logic, no runtime imports: `npm test` runs it.
import type { Segment } from '../types/index.ts'

/** Measured 2026-10-23 on the team's voices made since 0.2.8 (median error 9%). */
export const PACE_SECS_PER_CHAR = 0.065
export const MIN_CLIP_SECS = 0.1
/** Auto-fit speeds a voice up at most to this. */
export const AUTOFIT_FASTEST = 1.25
const EPS = 0.001

const round6 = (x: number) => Math.round(x * 1e6) / 1e6

type Voiced = Pick<Segment, 'start_time' | 'end_time' | 'tts_audio_url' | 'tts_duration_secs' | 'voice_offset' | 'fit_rate'>

/** How long a line's Khmer text takes to say, at the measured pace. */
export function estimateSecs(text: string): number {
  return Math.max(MIN_CLIP_SECS, Math.round((text ?? '').trim().length * PACE_SECS_PER_CHAR * 1000) / 1000)
}

export function isVoiced(seg: Pick<Segment, 'tts_audio_url' | 'tts_duration_secs'>): boolean {
  return !!seg.tts_audio_url && (seg.tts_duration_secs ?? 0) > 0
}

const offsetOf = (seg: Pick<Segment, 'voice_offset'>) => seg.voice_offset ?? 0
const rateOf = (seg: Pick<Segment, 'fit_rate'>) => seg.fit_rate || 1

/** The speed the clip's voice is heard at: the file's own speed × the clip's rate. */
export function effectiveSpeed(seg: Pick<Segment, 'voice_speed' | 'fit_rate'>): number {
  return (seg.voice_speed || 1) * rateOf(seg)
}

/** The latest a voiced clip can end: where its voice runs out. */
export function maxEnd(seg: Voiced): number {
  return round6(seg.start_time + ((seg.tts_duration_secs ?? 0) - offsetOf(seg)) / rateOf(seg))
}

/** Drag a voiced clip's left edge: cuts (or uncovers) sound at the start, never the speed. */
export function trimStart(seg: Voiced, newStart: number): { start_time: number; voice_offset: number } {
  const rate = rateOf(seg)
  const offset = offsetOf(seg)
  const tts = seg.tts_duration_secs ?? 0
  const wanted = offset + (Math.max(0, newStart) - seg.start_time) * rate
  const latest = Math.min(tts - MIN_CLIP_SECS * rate, offset + (seg.end_time - MIN_CLIP_SECS - seg.start_time) * rate)
  const voiceOffset = Math.max(0, Math.min(wanted, latest))
  return {
    start_time: round6(seg.start_time + (voiceOffset - offset) / rate),
    voice_offset: round6(voiceOffset),
  }
}

/** Drag a voiced clip's right edge: cuts (or uncovers) sound at the end. */
export function trimEnd(seg: Voiced, newEnd: number): { end_time: number } {
  return { end_time: round6(Math.max(seg.start_time + MIN_CLIP_SECS, Math.min(newEnd, maxEnd(seg)))) }
}

/** Where in its voice file a clip is at timeline time `t`, or null when it plays nothing then. */
export function voiceAt(seg: Voiced, t: number): { filePos: number; rate: number } | null {
  if (!isVoiced(seg) || t < seg.start_time || t >= seg.end_time) return null
  return { filePos: round6(offsetOf(seg) + (t - seg.start_time) * rateOf(seg)), rate: rateOf(seg) }
}

/** Every voice heard at `t` — overlapping clips play together, as in the export. */
export function audibleAt<S extends Voiced>(segs: S[], t: number): { seg: S; filePos: number; rate: number }[] {
  const heard: { seg: S; filePos: number; rate: number }[] = []
  for (const seg of segs) {
    const at = voiceAt(seg, t)
    if (at) heard.push({ seg, ...at })
  }
  return heard
}

/**
 * The voice file's address, versioned by everything that re-renders it in
 * place (the backend overwrites the same path): its length, volume and filter.
 * Without the volume a volume change kept playing the old, cached file.
 */
export function voiceUrl(seg: Pick<Segment, 'tts_audio_url' | 'tts_duration_secs' | 'volume_db' | 'voice_filter'>): string {
  const versioned = `${seg.tts_audio_url}?v=${seg.tts_duration_secs ?? 0}-${seg.volume_db ?? 0}-${seg.voice_filter ?? ''}`
  return versioned.startsWith('/') ? versioned : `/${versioned}`
}

/** The voice was made from text that has been edited since. */
export function isVoiceOutdated(seg: Pick<Segment, 'tts_audio_url' | 'tts_duration_secs' | 'voice_text' | 'khmer_text'>): boolean {
  return isVoiced(seg) && seg.voice_text != null && seg.voice_text !== seg.khmer_text
}

/**
 * The parts of each voice that run into a later line (any track) where the
 * original didn't overlap: from max(later start, where the actor stopped) to
 * min(this end, later end). Only voiced lines with an actor span have them.
 */
export function overrunSpans(segs: (Voiced & Pick<Segment, 'id' | 'source_end'>)[]): Record<string, [number, number][]> {
  const out: Record<string, [number, number][]> = {}
  for (const seg of segs) {
    if (!isVoiced(seg) || seg.source_end == null) continue
    const spans: [number, number][] = []
    for (const later of segs) {
      if (later.id === seg.id || later.start_time <= seg.start_time + EPS) continue
      const from = Math.max(later.start_time, seg.source_end)
      const to = Math.min(seg.end_time, later.end_time)
      if (to - from > EPS) spans.push([from, to])
    }
    if (!spans.length) continue
    spans.sort((a, b) => a[0] - b[0])
    const merged: [number, number][] = [spans[0]]
    for (const [from, to] of spans.slice(1)) {
      const last = merged[merged.length - 1]
      if (from <= last[1] + EPS) last[1] = Math.max(last[1], to)
      else merged.push([from, to])
    }
    out[seg.id] = merged.map(([a, b]) => [round6(a), round6(b)])
  }
  return out
}

/** The part of the voice file a clip plays, as fractions of the file. */
export function waveformSlice(seg: Voiced): { from: number; to: number } {
  const tts = seg.tts_duration_secs || 0
  if (tts <= 0) return { from: 0, to: 1 }
  const clamp = (x: number) => Math.max(0, Math.min(1, round6(x)))
  const offset = offsetOf(seg)
  return { from: clamp(offset / tts), to: clamp((offset + (seg.end_time - seg.start_time) * rateOf(seg)) / tts) }
}

// ── 0.3.1: fades and the volume line ──────────────────────────────────────────

/** The volume a clip can be given — the transcript panel's range (timeline.VOLUME_MIN_DB/MAX_DB). */
export const VOLUME_MIN_DB = -20
export const VOLUME_MAX_DB = 10

type Faded = Pick<Segment, 'start_time' | 'end_time' | 'fade_in_secs' | 'fade_out_secs'>

/** The clip's fade gain at timeline time `t` (linear, like the export's afade). */
export function fadeGainAt(seg: Faded, t: number): number {
  if (t < seg.start_time || t >= seg.end_time) return 0
  const fadeIn = seg.fade_in_secs ?? 0
  const fadeOut = seg.fade_out_secs ?? 0
  let gain = 1
  if (fadeIn > 0) gain = Math.min(gain, (t - seg.start_time) / fadeIn)
  if (fadeOut > 0) gain = Math.min(gain, (seg.end_time - t) / fadeOut)
  return Math.max(0, round6(gain))
}

/** New fades for a clip: never negative; the one being dragged stops where the other starts. */
export function clampFades(seg: Faded, fadeIn: number, fadeOut: number): { fade_in_secs: number; fade_out_secs: number } {
  const length = Math.max(0, seg.end_time - seg.start_time)
  const r3 = (x: number) => Math.round(x * 1000) / 1000
  let fi = Math.max(0, Math.min(length, fadeIn))
  let fo = Math.max(0, Math.min(length, fadeOut))
  if (fi + fo > length + EPS) {
    const inMoved = Math.abs(fi - (seg.fade_in_secs ?? 0)) > 1e-9
    const outMoved = Math.abs(fo - (seg.fade_out_secs ?? 0)) > 1e-9
    if (inMoved && !outMoved) fi = length - fo
    else if (outMoved && !inMoved) fo = length - fi
    else { const k = length / (fi + fo); fi *= k; fo *= k }
  }
  return { fade_in_secs: r3(fi), fade_out_secs: r3(fo) }
}

/** Where the volume line sits in its band: 0 = the quietest (bottom), 1 = the loudest (top). */
export function volumeToFraction(db: number): number {
  return Math.max(0, Math.min(1, (db - VOLUME_MIN_DB) / (VOLUME_MAX_DB - VOLUME_MIN_DB)))
}

/** The volume (whole dB) for a place on the line. */
export function fractionToVolume(fraction: number): number {
  const f = Math.max(0, Math.min(1, fraction))
  return Math.round(VOLUME_MIN_DB + f * (VOLUME_MAX_DB - VOLUME_MIN_DB))
}

// ── Changes to a selection ───────────────────────────────────────────────────

/** The clips a change made on `id` applies to: the whole selection when `id` is part of it, like CapCut. */
export function selectionTargets(selected: string[], id: string): string[] {
  return selected.length > 1 && selected.includes(id) ? selected : [id]
}

/** A clip's volume moved by `delta` dB with the rest of a selection: whole dB, inside the line's range. */
export function shiftedVolume(db: number, delta: number): number {
  return Math.round(Math.max(VOLUME_MIN_DB, Math.min(VOLUME_MAX_DB, db + delta)))
}

/** Shift+click: add or remove `id`; after a plain click on `anchor`, that clip joins too (like CapCut). */
export function toggleInSelection(selected: string[], anchor: string | null, id: string): string[] {
  if (selected.includes(id)) return selected.filter((x) => x !== id)
  const start = selected.length === 0 && anchor && anchor !== id ? [anchor] : selected
  return [...start, id]
}
