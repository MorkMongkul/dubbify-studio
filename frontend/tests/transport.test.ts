// Keyboard transport (src/lib/transport.ts): J/K/L, frame steps, clip edges.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { frameSecs, isTypingTarget, nextEdge, shuttle } from '../src/lib/transport.ts'

test('shuttle doubles up to 4x and K stops', () => {
  assert.equal(shuttle(0, 'l'), 1)
  assert.equal(shuttle(1, 'l'), 2)
  assert.equal(shuttle(2, 'l'), 4)
  assert.equal(shuttle(4, 'l'), 4)
  assert.equal(shuttle(-2, 'l'), 1)
  assert.equal(shuttle(1, 'j'), -1)
  assert.equal(shuttle(-1, 'j'), -2)
  assert.equal(shuttle(-4, 'j'), -4)
  assert.equal(shuttle(-2, 'k'), 0)
})

test('frame step falls back to 1/30', () => {
  assert.equal(frameSecs(25), 0.04)
  assert.equal(frameSecs(null), 1 / 30)
  assert.equal(frameSecs(undefined), 1 / 30)
  assert.equal(frameSecs(1000), 1 / 30)
})

test('nextEdge finds the next clip start or end', () => {
  const segs = [{ start_time: 1, end_time: 2 }, { start_time: 1.5, end_time: 4 }]
  assert.equal(nextEdge(segs, 0, 1), 1)
  assert.equal(nextEdge(segs, 1, 1), 1.5)
  assert.equal(nextEdge(segs, 2, 1), 4)
  assert.equal(nextEdge(segs, 4, 1), null)
  assert.equal(nextEdge(segs, 4, -1), 2)
  assert.equal(nextEdge(segs, 1, -1), null)
})

test('keys typed into a text box are not shortcuts', () => {
  assert.equal(isTypingTarget({ tagName: 'INPUT' } as unknown as EventTarget), true)
  assert.equal(isTypingTarget({ tagName: 'TEXTAREA' } as unknown as EventTarget), true)
  assert.equal(isTypingTarget({ tagName: 'DIV', isContentEditable: true } as unknown as EventTarget), true)
  assert.equal(isTypingTarget({ tagName: 'BUTTON' } as unknown as EventTarget), false)
  assert.equal(isTypingTarget(null), false)
})
