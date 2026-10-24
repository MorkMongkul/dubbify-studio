// Keeping the preview's sound with the picture (src/lib/playbackSync.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { syncAction } from '../src/lib/playbackSync.ts'

test('just started: the picture is left to settle', () => {
  assert.equal(syncAction(-0.5, 300), 'none')
})

test('a drift the eye forgives is left alone: sound up to 45 ms early or 125 ms late', () => {
  // drift = picture − sound heard: positive = the sound is late
  assert.equal(syncAction(0.1, 5000), 'none')
  assert.equal(syncAction(-0.03, 5000), 'none')
})

test('beyond that, the sound starts again from where the picture is', () => {
  assert.equal(syncAction(0.2, 5000), 'resync')
  assert.equal(syncAction(-0.06, 5000), 'resync')
})
