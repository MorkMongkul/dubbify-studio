// Quick successive mutations each get their own undo step and toast: mutateAsync, never mutate's own callbacks.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { MutationObserver, QueryClient } from '@tanstack/query-core'

const settle = () => new Promise((r) => setTimeout(r, 20))

test("TanStack calls mutate's own callbacks for the last of two quick calls only", async () => {
  const observer = new MutationObserver(new QueryClient(), {
    mutationFn: async (n: number) => { await new Promise((r) => setTimeout(r, 5)); return n },
  })
  const stop = observer.subscribe(() => {})   // as a mounted component's useMutation
  const viaCallbacks: number[] = []
  await Promise.all([1, 2].map((n) => observer.mutate(n, { onSuccess: (got) => { viaCallbacks.push(got) } })))
  await settle()
  assert.deepEqual(viaCallbacks, [2])         // the first Merge's undo step and toast were lost

  const awaited: number[] = []                // mutateAsync: each call awaits its own result
  await Promise.all([1, 2].map(async (n) => { awaited.push(await observer.mutate(n)) }))
  assert.deepEqual(awaited.sort(), [1, 2])
  stop()
})

test('the Speakers panel records every Merge, however quick ("Same character?" offers several in a row)', () => {
  const panel = readFileSync(new URL('../src/components/panels/SpeakersPanel.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(panel, /mergeSpeaker\.mutate\(/)
  assert.match(panel, /mutateAsync: mergeSpeaker/)
})
