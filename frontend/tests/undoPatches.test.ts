// What an undo sends back (src/lib/undoPatches.ts): every setting the edit may have changed.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { layoutPlace, splitUndoPatch } from '../src/lib/undoPatches.ts'
import { voiceUrl } from '../src/lib/clipRules.ts'
import type { Segment } from '../src/types/index.ts'

const seg = (extra: Partial<Segment> = {}): Segment => ({
  id: 'a', job_id: 'j', speaker_id: null, voice_id: null, lane_index: 2, start_time: 1, end_time: 3,
  source_text: '你好', english_text: '', khmer_text: 'សួស្តី', tts_audio_path: '/a.wav', tts_audio_url: '/uploads/a.wav',
  tts_duration_secs: 2, is_approved: false, notes: '', volume_db: 0, voice_filter: '', voice_speed: 1,
  source_start: 1, source_end: 3, voice_offset: 0.25, fit_rate: 1, voice_text: 'សួស្តី',
  fade_in_secs: 0.5, fade_out_secs: 0.7, ...extra,
})

test('a layout undo puts the fades back too (a trim may have shortened them)', () => {
  assert.deepEqual(layoutPlace(seg()), {
    id: 'a', start_time: 1, end_time: 3, lane_index: 2, voice_offset: 0.25, fade_in_secs: 0.5, fade_out_secs: 0.7,
  })
})

test('a split undo gives the first half back its fades', () => {
  const patch = splitUndoPatch(seg())
  assert.equal(patch.fade_in_secs, 0.5)
  assert.equal(patch.fade_out_secs, 0.7)
  assert.equal(patch.end_time, 3)
  assert.equal(patch.khmer_text, 'សួស្តី')
})

test('a voice re-rendered at another volume or filter gets a new address', () => {
  const a = voiceUrl(seg())
  assert.notEqual(voiceUrl(seg({ volume_db: 6 })), a)
  assert.notEqual(voiceUrl(seg({ voice_filter: 'echo' })), a)
  assert.notEqual(voiceUrl(seg({ tts_duration_secs: 2.5 })), a)
  assert.ok(a.startsWith('/uploads/a.wav?v='))
})
