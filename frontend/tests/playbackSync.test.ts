// Keeping an audio player with the video without cutting its sound (src/lib/playbackSync.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { syncAction } from '../src/lib/playbackSync.ts'

test('a player that just jumped is left to settle', () => {
  assert.deepEqual(syncAction(-0.3, 200), { kind: 'none' })
})

test('a small drift is caught up by playing a little faster or slower, never by a jump', () => {
  const behind = syncAction(-0.2, 2000)
  assert.equal(behind.kind, 'nudge')
  assert.ok(behind.kind === 'nudge' && behind.factor > 1 && behind.factor <= 1.05)
  const ahead = syncAction(0.2, 2000)
  assert.ok(ahead.kind === 'nudge' && ahead.factor < 1 && ahead.factor >= 0.95)
})

test('in step: nothing to do', () => {
  assert.deepEqual(syncAction(0.02, 2000), { kind: 'none' })
})

test('far off (a seek elsewhere): jump', () => {
  assert.deepEqual(syncAction(0.8, 2000), { kind: 'jump' })
  assert.deepEqual(syncAction(-0.6, 2000), { kind: 'jump' })
})
