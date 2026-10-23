// The editor's timing rules (src/lib/clipRules.ts) — the same rules as backend/app/services/timeline.py.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  audibleAt, effectiveSpeed, estimateSecs, isVoiceOutdated, isVoiced, maxEnd, overrunSpans, trimEnd, trimStart,
  voiceAt, waveformSlice,
} from '../src/lib/clipRules.ts'
import type { Segment } from '../src/types/index.ts'

let n = 0
type Opts = {
  start?: number; end?: number; tts?: number; offset?: number; fit?: number; vs?: number
  source?: [number, number]; text?: string; voiceText?: string | null; lane?: number
}
function seg(o: Opts): Segment {
  n += 1
  const tts = o.tts ?? 0
  return {
    id: `s${n}`, job_id: 'j', speaker_id: null, voice_id: null, lane_index: o.lane ?? 0,
    start_time: o.start ?? 0, end_time: o.end ?? (o.start ?? 0) + 1,
    source_text: '', english_text: '', khmer_text: o.text ?? '',
    tts_audio_path: tts ? '/x.wav' : '', tts_audio_url: tts ? '/uploads/x.wav' : null, tts_duration_secs: tts,
    is_approved: false, notes: '', volume_db: 0, voice_filter: '', voice_speed: o.vs ?? 1,
    source_start: o.source?.[0] ?? null, source_end: o.source?.[1] ?? null,
    voice_offset: o.offset ?? 0, fit_rate: o.fit ?? 1, voice_text: o.voiceText ?? null,
  }
}

test('the timing constants match the backend', () => {
  const py = readFileSync(new URL('../../backend/app/services/timeline.py', import.meta.url), 'utf8')
  assert.match(py, /PACE_SECS_PER_CHAR = 0\.065\b/)
  assert.match(py, /AUTOFIT_FASTEST = 1\.25\b/)
  assert.match(py, /MIN_CLIP_SECS = 0\.1\b/)
})

test('estimate follows the measured pace', () => {
  assert.equal(estimateSecs('ក'.repeat(20)), 1.3)
  assert.equal(estimateSecs(''), 0.1)
  assert.equal(estimateSecs('   '), 0.1)
})

test('a clip is voiced only with a file and a length', () => {
  assert.equal(isVoiced(seg({ tts: 1 })), true)
  assert.equal(isVoiced(seg({})), false)
})

test('the speed heard is the file speed times the clip rate', () => {
  assert.equal(effectiveSpeed(seg({ vs: 1.1, fit: 1.2 })), 1.1 * 1.2)
})

test('maxEnd counts offset and rate', () => {
  assert.equal(maxEnd(seg({ start: 2, tts: 3, offset: 0.5, fit: 1.25 })), 4)
})

test('trims clamp to the voice and the minimum', () => {
  const s = seg({ start: 1, end: 3, tts: 2 })
  assert.deepEqual(trimStart(s, 1.5), { start_time: 1.5, voice_offset: 0.5 })
  // past the other edge: stops 0.1 s before the end
  assert.deepEqual(trimStart(s, 9), { start_time: 2.9, voice_offset: 1.9 })
  // before the voice starts: nothing to uncover
  assert.deepEqual(trimStart(s, 0.2), { start_time: 1, voice_offset: 0 })
  assert.deepEqual(trimEnd(s, 9), { end_time: 3 })
  assert.deepEqual(trimEnd(s, 0), { end_time: 1.1 })
  // a trimmed start can be dragged back out, at its rate
  const t = seg({ start: 2, end: 3, tts: 3, offset: 1, fit: 2 })
  assert.deepEqual(trimStart(t, 1.6), { start_time: 1.6, voice_offset: 0.2 })
  assert.deepEqual(trimStart(t, 1), { start_time: 1.5, voice_offset: 0 })
  // never before 0 on the timeline
  assert.deepEqual(trimStart(seg({ start: 0.2, end: 1, tts: 2, offset: 1 }), -1), { start_time: 0, voice_offset: 0.8 })
})

test('voiceAt maps timeline time to the file', () => {
  assert.deepEqual(voiceAt(seg({ start: 1, end: 2, offset: 0.5, fit: 1.25, tts: 3 }), 1.4), { filePos: 1, rate: 1.25 })
  assert.equal(voiceAt(seg({ start: 1, end: 2, tts: 3 }), 2.0), null)
  assert.equal(voiceAt(seg({ start: 1, end: 2, tts: 3 }), 0.9), null)
  assert.equal(voiceAt(seg({ start: 1, end: 2 }), 1.5), null)
})

test('two overlapping voices are both audible', () => {
  const a = seg({ start: 0, end: 2, tts: 2 })
  const b = seg({ start: 1, end: 3, tts: 2, lane: 1 })
  const c = seg({ start: 1, end: 3 })
  const heard = audibleAt([a, b, c], 1.5)
  assert.deepEqual(heard.map((h) => h.seg.id), [a.id, b.id])
  assert.deepEqual(heard.map((h) => h.filePos), [1.5, 0.5])
})

test('an edited line marks its voice outdated', () => {
  assert.equal(isVoiceOutdated(seg({ tts: 1, text: 'new', voiceText: 'old' })), true)
  assert.equal(isVoiceOutdated(seg({ tts: 1, text: 'same', voiceText: 'same' })), false)
  assert.equal(isVoiceOutdated(seg({ tts: 1, text: 'x', voiceText: null })), false)
  assert.equal(isVoiceOutdated(seg({ text: 'x', voiceText: 'y' })), false)
})

test('red spans skip overlaps the original had', () => {
  const a = seg({ start: 0, end: 3.8, tts: 3.8, source: [0, 3.4] })
  const b = seg({ start: 3, end: 5, lane: 1 })
  assert.deepEqual(overrunSpans([a, b]), { [a.id]: [[3.4, 3.8]] })
  // ends before the actor stopped: nothing red
  const c = seg({ start: 0, end: 3.3, tts: 3.3, source: [0, 3.4] })
  assert.deepEqual(overrunSpans([c, b]), {})
  // two characters who really talk at once in the original are not red
  const d = seg({ start: 0, end: 2, tts: 2, source: [0, 2] })
  const e = seg({ start: 1, end: 2.5, tts: 1.5, source: [1, 2.5], lane: 1 })
  assert.deepEqual(overrunSpans([d, e]), {})
  // only up to the end of the later line, merged across lines
  const f = seg({ start: 0, end: 5, tts: 5, source: [0, 1] })
  const g = seg({ start: 2, end: 2.5, lane: 1 })
  const h = seg({ start: 2.4, end: 3, lane: 2 })
  assert.deepEqual(overrunSpans([f, g, h]), { [f.id]: [[2, 3]] })
  // a line added by hand (no span of its own) and a voiceless line have none
  assert.deepEqual(overrunSpans([seg({ start: 0, end: 3, tts: 3 }), b]), {})
  assert.deepEqual(overrunSpans([seg({ start: 0, end: 4, source: [0, 1] }), b]), {})
})

test('the waveform shows only the part of the voice the clip plays', () => {
  assert.deepEqual(waveformSlice(seg({ start: 1, end: 2, tts: 4, offset: 1, fit: 2 })), { from: 0.25, to: 0.75 })
  assert.deepEqual(waveformSlice(seg({ start: 0, end: 2, tts: 2 })), { from: 0, to: 1 })
})
