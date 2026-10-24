// The music dips under the voices (src/lib/ducking.ts) — the same rule as backend/app/services/timeline.py.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { DUCK_DB, DUCK_RAMP_SECS, duckGainAt, duckSpans } from '../src/lib/ducking.ts'

// (t, gain) for one voice at 1.0–2.0 s — the same table is in backend/app/tests/test_sound_shaping.py
const DUCK_TABLE: [number, number][] = [[0.7, 1], [0.8, 1], [0.9, 0.6991], [1.0, 0.3981], [1.5, 0.3981], [2.0, 0.3981], [2.1, 0.6991], [2.2, 1]]

const clip = (start: number, end: number, voiced = true) => ({
  start_time: start, end_time: end, tts_audio_url: voiced ? '/u.wav' : null, tts_duration_secs: voiced ? 1 : 0,
})

test('the ducking constants match the backend', () => {
  const py = readFileSync(new URL('../../backend/app/services/timeline.py', import.meta.url), 'utf8')
  assert.match(py, new RegExp(`DUCK_DB = ${DUCK_DB.toFixed(1)}\\b`))
  assert.match(py, new RegExp(`DUCK_RAMP_SECS = ${DUCK_RAMP_SECS}\\b`))
})

test('ducking gain matches the shared table', () => {
  const spans = duckSpans([clip(1, 2)])
  assert.deepEqual(spans, [[1, 2]])
  for (const [t, g] of DUCK_TABLE) assert.ok(Math.abs(duckGainAt(spans, t) - g) < 1e-4, `${t}: ${duckGainAt(spans, t)} vs ${g}`)
  assert.equal(duckGainAt([], 1), 1)
})

test('close voices duck as one span; voiceless lines do not duck', () => {
  assert.deepEqual(duckSpans([clip(2.3, 3), clip(1, 2), clip(5, 6, false)]), [[1, 3]])
  assert.deepEqual(duckSpans([clip(1, 2), clip(2.5, 3)]), [[1, 2], [2.5, 3]])
})
