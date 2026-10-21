// Run with `npm test` (Node's own test runner; Node strips the TypeScript types).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { standInMessage, withTurn } from '../src/lib/chatMessages.ts'

const saved = (id: string, role: 'user' | 'model', text: string) =>
  ({ id, project_id: 'p1', role, text, created_at: '2026-10-21T05:00:00Z' })

test('what you send shows at once, on your side of the chat', () => {
  const sent = standInMessage('p1', 'Fix line 3', new Date('2026-10-21T05:00:00Z'))
  assert.equal(sent.role, 'user')
  assert.equal(sent.text, 'Fix line 3')
  assert.equal(sent.project_id, 'p1')
  assert.notEqual(standInMessage('p1', 'Fix line 4', new Date('2026-10-21T05:00:09Z')).id, sent.id)
})

test("when the reply comes, the saved message takes the stand-in's place, followed by the reply", () => {
  const before = [saved('a', 'user', 'hi'), saved('b', 'model', 'Hello')]
  const sent = standInMessage('p1', 'Fix line 3', new Date())
  const turn = [saved('c', 'user', 'Fix line 3'), saved('d', 'model', 'Done')]
  assert.deepEqual(withTurn([...before, sent], sent.id, turn).map((m) => m.id), ['a', 'b', 'c', 'd'])
})

test('a message the list already has is not shown twice', () => {
  // The panel was reopened while the Assistant worked, and the list it fetched
  // already had the saved question
  const list = [saved('a', 'user', 'hi'), saved('c', 'user', 'Fix line 3')]
  const turn = [saved('c', 'user', 'Fix line 3'), saved('d', 'model', 'Done')]
  assert.deepEqual(withTurn(list, 'sending-1', turn).map((m) => m.id), ['a', 'c', 'd'])
})

test('a failed send takes the stand-in away', () => {
  const sent = standInMessage('p1', 'Fix line 3', new Date())
  assert.deepEqual(withTurn([saved('a', 'user', 'hi'), sent], sent.id, []).map((m) => m.id), ['a'])
})
