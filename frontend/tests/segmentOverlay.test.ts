// The editor shows server segments with in-flight edits laid over them (src/lib/segmentOverlay.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mergeOverlay } from '../src/lib/segmentOverlay.ts'
import type { Segment } from '../src/types/index.ts'

const seg = (id: string, extra: Partial<Segment> = {}): Segment => ({
  id, job_id: 'j', speaker_id: null, voice_id: null, lane_index: 0, start_time: 0, end_time: 1,
  source_text: '', english_text: '', khmer_text: 'server', tts_audio_path: '/a.wav', tts_audio_url: '/uploads/a.wav',
  tts_duration_secs: 1, is_approved: false, notes: '', volume_db: 0, voice_filter: '', voice_speed: 1,
  voice_offset: 0, fit_rate: 1, voice_text: 'server', ...extra,
})

test('in-flight start, end, track and trim apply over the server', () => {
  const [a] = mergeOverlay([seg('a')], { a: { start_time: 2, end_time: 3, lane_index: 4, voice_offset: 0.5 } })
  assert.deepEqual([a.start_time, a.end_time, a.lane_index, a.voice_offset], [2, 3, 4, 0.5])
})

test('text typed but not saved shows over the server', () => {
  const [a] = mergeOverlay([seg('a')], { a: { khmer_text: 'typing' } })
  assert.equal(a.khmer_text, 'typing')
})

test('the voice always comes from the server', () => {
  const [a] = mergeOverlay([seg('a')], {
    a: { tts_audio_path: '', tts_duration_secs: 0, tts_audio_url: null, voice_speed: 2, voice_text: 'x', fit_rate: 3 },
  })
  assert.deepEqual([a.tts_audio_path, a.tts_duration_secs, a.tts_audio_url, a.voice_speed, a.voice_text, a.fit_rate],
    ['/a.wav', 1, '/uploads/a.wav', 1, 'server', 1])
})

test('a segment without an overlay is the same object', () => {
  const b = seg('b')
  const out = mergeOverlay([seg('a'), b], { a: { start_time: 2 } })
  assert.equal(out[1], b)
  const none = [seg('c')]
  assert.equal(mergeOverlay(none, {})[0], none[0])
})
