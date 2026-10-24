// src/lib/trackFlags.ts
// Each voice track's switches, like CapCut's track headers (the server keeps
// them in jobs.track_flags, backend/app/services/timeline.py): locked = no
// edits; muted = its voices aren't played or exported (hide went with the
// subtitles, 0.4.0). A track is "audio" once one of its lines has a
// voice, "text" before that. Pure — covered by `npm test`.
import type { LayoutChange } from './tracks.ts'

export interface TrackSwitches { locked: boolean; muted: boolean }
/** lane_index → switches; only tracks with a switch on. */
export type TrackFlagMap = Record<string, TrackSwitches>
export type SwitchKey = keyof TrackSwitches

export const NO_SWITCHES: TrackSwitches = { locked: false, muted: false }

type Lined = { lane_index?: number | null; tts_audio_url?: string | null; tts_duration_secs?: number | null }
const laneOf = (s: { lane_index?: number | null }) => s.lane_index ?? 0
const anyOn = (f: TrackSwitches) => f.locked || f.muted

export function switchesOf(map: TrackFlagMap | null | undefined, lane: number): TrackSwitches {
  return { ...NO_SWITCHES, ...(map?.[String(lane)] ?? {}) }
}

/** A new map with one switch changed; tracks with nothing on are dropped. */
export function withSwitch(map: TrackFlagMap | null | undefined, lane: number, key: SwitchKey, on: boolean): TrackFlagMap {
  const next: TrackFlagMap = {}
  for (const [k, f] of Object.entries(map ?? {})) if (anyOn({ ...NO_SWITCHES, ...f })) next[k] = { ...NO_SWITCHES, ...f }
  const f = { ...switchesOf(next, lane), [key]: on }
  if (anyOn(f)) next[String(lane)] = f
  else delete next[String(lane)]
  return next
}

export function trackKind(segs: Lined[], lane: number): 'audio' | 'text' {
  return segs.some((s) => laneOf(s) === lane && !!s.tts_audio_url && (s.tts_duration_secs ?? 0) > 0) ? 'audio' : 'text'
}

/** The tracks a drop moved down to make room (lib/tracks planDrop), or null. */
export function laneShift(
  segs: { id: string; lane_index?: number | null }[], changes: LayoutChange[], moving: string[],
): { from: number; by: number } | null {
  const ids = new Set(moving)
  const shifted = changes.filter((c) => !ids.has(c.id) && c.lane_index != null)
    .map((c) => ({ from: laneOf(segs.find((s) => s.id === c.id) ?? {}), to: c.lane_index! }))
    .filter((x) => x.to !== x.from)
  if (!shifted.length) return null
  return { from: Math.min(...shifted.map((x) => x.from)), by: shifted[0].to - shifted[0].from }
}

/** Switches of tracks at or below `from` move down by `by`, with their tracks. */
export function shiftSwitches(map: TrackFlagMap | null | undefined, from: number, by: number): TrackFlagMap {
  const next: TrackFlagMap = {}
  for (const [k, f] of Object.entries(map ?? {})) {
    const lane = Number(k)
    next[String(lane >= from ? lane + by : lane)] = { ...NO_SWITCHES, ...f }
  }
  return next
}

/** What the export window should say it leaves out ("Track N" counts the drawn rows from the top). */
export function exportWarnings(rows: number[], map: TrackFlagMap | null | undefined): string[] {
  const out: string[] = []
  rows.forEach((lane, i) => {
    if (switchesOf(map, lane).muted) out.push(`Track ${i + 1} is muted: its voices won't be in the export`)
  })
  return out
}
