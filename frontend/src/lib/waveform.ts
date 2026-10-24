// src/lib/waveform.ts
// How a voice clip's or a stem piece's waveform follows its volume line, like
// CapCut: bars are drawn at the sound's real level (not stretched to fill the
// clip), grow and shrink while the line is dragged, and turn "loud" where they
// pass the export's limit. Pure — covered by `npm test`.
import { shiftedVolume, volumeToFraction } from './clipRules.ts'

/** −1 dBFS: the export never lets a voice peak above this (voice_level.CEILING_DB). */
export const CEILING = 10 ** (-1 / 20)

/** The thinnest bar drawn, as a fraction of the band: silence reads as a line. */
const FLOOR = 0.04

/** Red starts a little past the limit: a voice the leveller holds at −1 dBFS (rounded to
 *  0.1 dB, plus what decoding adds) is fine, not too loud. */
const LOUD = CEILING * 10 ** (0.3 / 20)

/** Pixels of drag per dB on a volume line, and the drag it takes to change anything at all. */
const PX_PER_DB = 2
const DEAD_ZONE_PX = 3

/** Amplitude factor for a gain in dB. */
export function gainFor(db: number): number {
  return 10 ** (db / 20)
}

/** Bar heights (fractions of the band, clamped to 1) and which bars pass the limit. */
export function levelBars(peaks: number[], gain: number): { h: number; loud: boolean }[] {
  return peaks.map((p) => {
    const level = p * gain
    return { h: Math.max(FLOOR, Math.min(1, level)), loud: level > LOUD }
  })
}

/** A volume line grabbed at `from` dB and dragged `dy` px (down is positive): 2 px per dB
 *  from where it was grabbed, so it never jumps and every whole dB is reachable; a
 *  click's jitter (under 3 px) changes nothing. */
export function volumeAfterDrag(from: number, dy: number): number {
  if (Math.abs(dy) < DEAD_ZONE_PX) return from
  return shiftedVolume(from, Math.round(-dy / PX_PER_DB))
}

/** How far down its band (px) the line sits for `db`. */
export function volumeTop(db: number, height: number): number {
  return Math.round((1 - volumeToFraction(db)) * height * 100) / 100
}

export function dbLabel(db: number): string {
  return `${db > 0 ? '+' : db < 0 ? '−' : ''}${Math.abs(db)} dB`
}

/** Absolute peaks (the loudest channel), `perSec` per second of audio. */
export function peaksFrom(channels: Float32Array[], sampleRate: number, perSec: number): number[] {
  const length = channels[0]?.length ?? 0
  const step = Math.max(1, Math.round(sampleRate / perSec))
  const peaks: number[] = []
  for (let start = 0; start < length; start += step) {
    const end = Math.min(length, start + step)
    let max = 0
    for (const data of channels) {
      for (let i = start; i < end; i++) {
        const v = Math.abs(data[i])
        if (v > max) max = v
      }
    }
    peaks.push(max)
  }
  return peaks
}

/** `count` bars for the time span [from, to) of peaks made `perSec` per second. */
export function slicePeaks(peaks: number[], perSec: number, from: number, to: number, count: number): number[] {
  const a = Math.max(0, Math.floor(from * perSec + 1e-9))
  const b = Math.min(peaks.length, Math.ceil(to * perSec - 1e-9))
  if (b <= a || count <= 0) return []
  const n = Math.min(count, b - a)
  const out: number[] = []
  for (let i = 0; i < n; i++) {
    const s = a + Math.floor((i * (b - a)) / n)
    const e = Math.max(s + 1, a + Math.floor(((i + 1) * (b - a)) / n))
    let max = 0
    for (let j = s; j < e; j++) if (peaks[j] > max) max = peaks[j]
    out.push(max)
  }
  return out
}
