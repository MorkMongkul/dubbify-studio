// The music & effects dip under the voices (0.3.1) — the same rule as
// backend/app/services/timeline.py (duck_spans / duck_gain_at), which the
// export applies as one ffmpeg `volume` expression. Pure logic: `npm test` runs it.

export const DUCK_DB = -8.0
export const DUCK_RAMP_SECS = 0.2

type Clip = { start_time: number; end_time: number; tts_audio_url?: string | null; tts_duration_secs?: number }

/** Where voices play, merged when two are closer than two ramps. */
export function duckSpans(segs: Clip[]): [number, number][] {
  const spans: [number, number][] = []
  const voiced = segs
    .filter((s) => !!s.tts_audio_url && (s.tts_duration_secs ?? 0) > 0)
    .map((s) => [s.start_time, s.end_time] as [number, number])
    .sort((a, b) => a[0] - b[0] || a[1] - b[1])
  for (const [a, b] of voiced) {
    const last = spans[spans.length - 1]
    if (last && a - last[1] < 2 * DUCK_RAMP_SECS) last[1] = Math.max(last[1], b)
    else spans.push([a, b])
  }
  return spans.map(([a, b]) => [Math.round(a * 1000) / 1000, Math.round(b * 1000) / 1000])
}

/** The music's gain at `t`: down by DUCK_DB under a voice, ramping before it starts and after it ends. */
export function duckGainAt(spans: [number, number][], t: number): number {
  const r = DUCK_RAMP_SECS
  let depth = 0
  for (const [a, b] of spans) {
    const up = Math.min(1, Math.max(0, (t - (a - r)) / r))
    const down = Math.min(1, Math.max(0, ((b + r) - t) / r))
    depth = Math.max(depth, up * down)
  }
  return 1 - (1 - 10 ** (DUCK_DB / 20)) * depth
}
