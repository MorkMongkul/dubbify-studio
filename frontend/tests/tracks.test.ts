// Track placement for drops (src/lib/tracks.ts): like CapCut, a drop never moves a clip in time.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { clipAtPlayhead, dropSpot, fitsOnLane, planDrop, trackRows } from '../src/lib/tracks.ts'
import type { LayoutChange, Placed } from '../src/lib/tracks.ts'

const p = (id: string, start: number, end: number, lane: number): Placed => ({ id, start_time: start, end_time: end, lane_index: lane })
const byId = (changes: LayoutChange[]) => Object.fromEntries(changes.map((c) => [c.id, c]))

test('empty tracks are hidden', () => {
  assert.deepEqual(trackRows([{ lane_index: 7 }, { lane_index: 0 }, { lane_index: 3 }, { lane_index: null }] as Placed[]), [0, 3, 7])
  assert.deepEqual(trackRows([]), [0])
})

test('a drop spot is a row, or a boundary near an edge', () => {
  assert.deepEqual(dropSpot(20, 3, 40), { kind: 'onto', row: 0 })
  assert.deepEqual(dropSpot(3, 3, 40), { kind: 'insert', boundary: 0 })
  assert.deepEqual(dropSpot(-10, 3, 40), { kind: 'insert', boundary: 0 })
  assert.deepEqual(dropSpot(38, 3, 40), { kind: 'insert', boundary: 1 })
  assert.deepEqual(dropSpot(43, 3, 40), { kind: 'insert', boundary: 1 })
  assert.deepEqual(dropSpot(60, 3, 40), { kind: 'onto', row: 1 })
  assert.deepEqual(dropSpot(118, 3, 40), { kind: 'insert', boundary: 3 })
  assert.deepEqual(dropSpot(400, 3, 40), { kind: 'insert', boundary: 3 })
})

test('fitsOnLane ignores the clips being moved', () => {
  const segs = [p('a', 0, 2, 0), p('b', 3, 4, 0)]
  assert.equal(fitsOnLane(segs, 0, 1, 2.5, new Set()), false)
  assert.equal(fitsOnLane(segs, 0, 1, 2.5, new Set(['a'])), true)
  assert.equal(fitsOnLane(segs, 0, 2, 3, new Set()), true)
})

test('drop onto a free spot moves time and track', () => {
  const segs = [p('a', 0, 1, 0), p('b', 0, 1, 1)]
  assert.deepEqual(planDrop(segs, ['a'], 'a', 2, { kind: 'onto', row: 1 }), [{ id: 'a', start_time: 2, end_time: 3, lane_index: 1 }])
})

test('a click without movement changes nothing', () => {
  assert.deepEqual(planDrop([p('a', 0, 1, 0)], ['a'], 'a', 0, { kind: 'onto', row: 0 }), [])
})

test('drop onto a taken spot goes to a new track below, same time', () => {
  const segs = [p('a', 0, 1, 0), p('b', 5, 7, 1), p('c', 5, 6, 2)]
  const changes = byId(planDrop(segs, ['a'], 'a', 5.5, { kind: 'onto', row: 1 }))
  // a new track between 1 and 2: c moves down, a takes the new track at the time it was dropped
  assert.deepEqual(changes.a, { id: 'a', start_time: 5.5, end_time: 6.5, lane_index: 2 })
  assert.deepEqual(changes.c, { id: 'c', lane_index: 3 })
  assert.equal(changes.b, undefined)
})

test('drop between two tracks inserts one, shifting lower tracks only when there is no gap', () => {
  const tight = [p('a', 0, 1, 0), p('b', 0, 1, 1), p('c', 0, 1, 2)]
  assert.deepEqual(byId(planDrop(tight, ['c'], 'c', 0, { kind: 'insert', boundary: 1 })), {
    c: { id: 'c', start_time: 0, end_time: 1, lane_index: 1 },
    b: { id: 'b', lane_index: 2 },
  })
  const gap = [p('a', 0, 1, 0), p('b', 0, 1, 2), p('c', 0, 1, 3)]
  assert.deepEqual(planDrop(gap, ['c'], 'c', 0, { kind: 'insert', boundary: 1 }), [{ id: 'c', start_time: 0, end_time: 1, lane_index: 1 }])
  // above the first track
  assert.deepEqual(byId(planDrop(tight, ['c'], 'c', 0, { kind: 'insert', boundary: 0 })), {
    c: { id: 'c', start_time: 0, end_time: 1, lane_index: 0 },
    a: { id: 'a', lane_index: 1 },
    b: { id: 'b', lane_index: 2 },
  })
  // below the last
  assert.deepEqual(planDrop(tight, ['a'], 'a', 0, { kind: 'insert', boundary: 3 }), [{ id: 'a', start_time: 0, end_time: 1, lane_index: 3 }])
})

test('a group keeps its relative tracks and timing', () => {
  const segs = [p('a', 1, 2, 0), p('b', 3, 4, 1), p('x', 0, 1, 3)]
  assert.deepEqual(byId(planDrop(segs, ['a', 'b'], 'b', 2, { kind: 'onto', row: 2 })), {
    a: { id: 'a', start_time: 3, end_time: 4, lane_index: 1 },
    b: { id: 'b', start_time: 5, end_time: 6, lane_index: 3 },
  })
  // the group lands on taken spots: new tracks below the grabbed clip's row, one per row of the group
  const taken = byId(planDrop(segs, ['a', 'b'], 'b', 2, { kind: 'onto', row: 2 }))
  assert.equal(taken.x, undefined)
  const crowded = [p('a', 1, 2, 0), p('b', 3, 4, 1), p('y', 3, 4, 1 + 1), p('z', 5, 6, 3)]
  const placed = byId(planDrop(crowded, ['a', 'b'], 'b', 2, { kind: 'onto', row: 3 }))
  assert.deepEqual([placed.a.lane_index, placed.b.lane_index], [4, 5])
  assert.deepEqual([placed.a.start_time, placed.b.start_time], [3, 5])
  // never before 0: the group stops at the start
  const early = byId(planDrop(segs, ['a', 'b'], 'a', -5, { kind: 'onto', row: 0 }))
  assert.deepEqual([early.a.start_time, early.b.start_time], [0, 2])
})

test('clipAtPlayhead prefers the active clip', () => {
  const segs = [p('a', 0, 2, 1), p('b', 1, 3, 0)]
  assert.equal(clipAtPlayhead(segs, 1.5, 'a')?.id, 'a')
  assert.equal(clipAtPlayhead(segs, 1.5, null)?.id, 'b')
  assert.equal(clipAtPlayhead(segs, 2.5, 'a')?.id, 'b')
  assert.equal(clipAtPlayhead(segs, 9, null), null)
})
