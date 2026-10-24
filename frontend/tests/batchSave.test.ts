// Saving a change to several clips (src/lib/batchSave.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { saveEach } from '../src/lib/batchSave.ts'

test('every clip is saved, a few at a time, in order', async () => {
  let running = 0, most = 0
  const { ok, failed } = await saveEach(['a', 'b', 'c', 'd', 'e', 'f'], async (id) => {
    running++; most = Math.max(most, running)
    await new Promise((r) => setTimeout(r, 5))
    running--
    return id.toUpperCase()
  }, 4)
  assert.deepEqual(ok, ['A', 'B', 'C', 'D', 'E', 'F'])
  assert.deepEqual(failed, [])
  assert.ok(most <= 4)
})

test('one failure keeps the others, so they still get their undo step', async () => {
  const { ok, failed } = await saveEach(['a', 'b', 'c', 'd', 'e'], async (id) => {
    if (id === 'b') throw new Error('nope')
    return id
  }, 2)
  assert.deepEqual(ok, ['a', 'c', 'd', 'e'])
  assert.deepEqual(failed, ['b'])
})
