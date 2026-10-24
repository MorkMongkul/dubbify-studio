// The editor's layouts, like CapCut's (src/lib/layouts.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LAYOUTS, layoutPlan, readLayout } from '../src/lib/layouts.ts'

const areas = (p: ReturnType<typeof layoutPlan>) => [...p.top, ...(p.tall ? [p.tall.area] : [])].sort()

test('every layout shows the panel, the player and the transcript once', () => {
  for (const { name } of LAYOUTS) assert.deepEqual(areas(layoutPlan(name)), ['panel', 'player', 'transcript'], name)
})

test('vertical: the player is as tall as the window on the right, the transcript in the middle', () => {
  const p = layoutPlan('vertical')
  assert.deepEqual(p.tall, { area: 'player', side: 'right' })
  assert.deepEqual(p.top, ['panel', 'transcript'])
  assert.equal(p.flex, 'transcript')
})

test('default is today\'s layout; the player takes the room left over wherever it sits above the timeline', () => {
  assert.deepEqual(layoutPlan('default'), { tall: null, top: ['panel', 'player', 'transcript'], flex: 'player' })
  assert.deepEqual(layoutPlan('transcript').tall, { area: 'transcript', side: 'right' })
  assert.equal(layoutPlan('transcript').flex, 'player')
  assert.deepEqual(layoutPlan('panel').tall, { area: 'panel', side: 'left' })
  assert.equal(layoutPlan('panel').flex, 'player')
})

test('a hidden transcript leaves no gap: what stays beside it takes the room', () => {
  assert.deepEqual(layoutPlan('vertical', true), { tall: { area: 'player', side: 'right' }, top: ['panel'], flex: 'panel' })
  assert.deepEqual(layoutPlan('transcript', true), { tall: null, top: ['panel', 'player'], flex: 'player' })
})

test('a saved layout is read back; anything else is the default', () => {
  assert.equal(readLayout('vertical'), 'vertical')
  assert.equal(readLayout(null), 'default')
  assert.equal(readLayout('sideways'), 'default')
})
