// Run with `npm test` (Node's own test runner; Node strips the TypeScript types).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { jobProgress } from '../src/lib/jobProgress.ts'

const states = (status: string, hasLines = false) =>
  jobProgress(status, hasLines)!.steps.map((s) => `${s.label}: ${s.state}`)

test('a first analysis says what the long step really does, in plain words', () => {
  const p = jobProgress('diarizing', false)!
  assert.equal(p.title, 'Transcribing and finding speakers…')
  assert.equal(p.detail, 'Writing down each line and who says it')
  assert.equal(jobProgress('transcribing', false)!.title, 'Transcribing and finding speakers…')
  assert.equal(jobProgress('translating', false)!.title, 'Translating to Khmer…')
  for (const status of ['diarizing', 'transcribing', 'translating']) {
    const p = jobProgress(status, false)!
    assert.doesNotMatch(`${p.title} ${p.detail} ${p.steps.map((s) => s.label)}`, /diariz/i)
  }
})

test('the dots show only the steps that run: analysis never claims voices or mixing', () => {
  assert.deepEqual(states('diarizing'), ['Transcribe and find speakers: active', 'Translate: pending'])
  assert.deepEqual(states('transcribing'), ['Transcribe and find speakers: active', 'Translate: pending'])
  assert.deepEqual(states('translating'), ['Transcribe and find speakers: done', 'Translate: active'])
  assert.deepEqual(states('pending'), ['Extract the audio: pending', 'Separate music and voices: pending'])
  assert.deepEqual(states('extracting'), ['Extract the audio: active', 'Separate music and voices: pending'])
  assert.deepEqual(states('separating'), ['Extract the audio: done', 'Separate music and voices: active'])
})

test('with lines on screen, the bar names the action that was started', () => {
  const detect = jobProgress('diarizing', true, 'detect-speakers')!
  assert.equal(detect.title, 'Detecting speakers…')
  assert.equal(detect.detail, 'Text, translations and voices stay as they are')
  assert.deepEqual(detect.steps, [])

  assert.equal(jobProgress('diarizing', true, 'analyze')!.title, 'Analyzing again: transcribing and finding speakers…')
  assert.equal(jobProgress('translating', true, 'analyze')!.title, 'Analyzing again: translating…')
  assert.equal(jobProgress('translating', true, 'analyze')!.detail, "These lines are replaced when it's done")

  assert.equal(jobProgress('translating', true, 'translate-missing')!.title, 'Translating missing lines…')

  const separating = jobProgress('separating', true)!
  assert.equal(separating.title, 'Separating music and voices…')
  assert.equal(separating.detail, 'Lines, translations and voices are kept')
  assert.deepEqual(separating.steps, [])
})

test("without a known action, or one that can't be running now, the bar claims nothing it doesn't know", () => {
  // After a reload the started action is unknown
  assert.equal(jobProgress('diarizing', true)!.title, 'Detecting speakers…')
  assert.equal(jobProgress('diarizing', true)!.detail, undefined)
  assert.equal(jobProgress('translating', true)!.title, 'Translating…')
  // Speaker detection never translates: an older action doesn't describe this run
  assert.equal(jobProgress('translating', true, 'detect-speakers')!.title, 'Translating…')
  assert.equal(jobProgress('diarizing', true, 'translate-missing')!.title, 'Detecting speakers…')
})

test('nothing to show when no step is running that this progress covers', () => {
  for (const status of ['stems_ready', 'completed', 'failed', 'synthesizing', 'mixing', '']) {
    assert.equal(jobProgress(status, false), null, status)
    assert.equal(jobProgress(status, true, 'analyze'), null, status)
  }
  assert.equal(jobProgress(undefined, false), null)
  assert.equal(jobProgress(null, true), null)
})
