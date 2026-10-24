// Each voice track's switches — lock, hide, mute — and its kind (src/lib/trackFlags.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  exportWarnings, laneShift, shiftSwitches, switchesOf, trackKind, withSwitch,
} from '../src/lib/trackFlags.ts'
import { planDrop } from '../src/lib/tracks.ts'

const OFF = { locked: false, hidden: false, muted: false }
const clip = (id: string, start: number, end: number, lane: number, voiced = false) =>
  ({ id, start_time: start, end_time: end, lane_index: lane, tts_audio_url: voiced ? '/x.wav' : null, tts_duration_secs: voiced ? end - start : 0 })

test('a track is audio once one of its lines has a voice, text before that', () => {
  const segs = [clip('a', 0, 1, 0), clip('b', 1, 2, 0, true), clip('c', 0, 1, 1)]
  assert.equal(trackKind(segs, 0), 'audio')
  assert.equal(trackKind(segs, 1), 'text')
})

test('switching keeps only tracks with a switch on', () => {
  const on = withSwitch({}, 2, 'muted', true)
  assert.deepEqual(on, { 2: { ...OFF, muted: true } })
  assert.deepEqual(switchesOf(on, 2), { ...OFF, muted: true })
  assert.deepEqual(switchesOf(on, 5), OFF)
  assert.deepEqual(withSwitch(on, 2, 'muted', false), {})
})

test('a drop that inserts a track moves the switches below it', () => {
  // tracks 0 and 1 (no gap): dropping between them shifts track 1 down by one
  const segs = [clip('a', 0, 1, 0), clip('b', 0, 1, 1), clip('m', 5, 6, 0)]
  const changes = planDrop(segs, ['m'], 'm', 0, { kind: 'insert', boundary: 1 })
  const shift = laneShift(segs, changes, ['m'])
  assert.deepEqual(shift, { from: 1, by: 1 })
  assert.deepEqual(shiftSwitches({ 0: { ...OFF, hidden: true }, 1: { ...OFF, locked: true } }, 1, 1),
    { 0: { ...OFF, hidden: true }, 2: { ...OFF, locked: true } })
  assert.equal(laneShift(segs, planDrop(segs, ['m'], 'm', 0, { kind: 'onto', row: 1 }), ['m']), null)
})

test('a drop onto a locked track makes a new track instead', () => {
  const segs = [clip('a', 0, 1, 0), clip('b', 0, 1, 1), clip('m', 5, 6, 0)]
  const changes = planDrop(segs, ['m'], 'm', 0, { kind: 'onto', row: 1 }, new Set([1]))
  const m = changes.find((c) => c.id === 'm')!
  assert.notEqual(m.lane_index, 1)
  assert.equal(m.start_time, 5)   // never moved in time
})

test('the export window names what will be left out', () => {
  const flags = { 1: { ...OFF, muted: true }, 4: { ...OFF, hidden: true, muted: true }, 7: { ...OFF, locked: true } }
  assert.deepEqual(exportWarnings([0, 1, 4, 7], flags), [
    "Track 2 is muted: its voices won't be in the export",
    "Track 3 is muted and hidden: its voices and subtitles won't be in the export",
  ])
  assert.deepEqual(exportWarnings([0], {}), [])
})
