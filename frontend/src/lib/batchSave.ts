// src/lib/batchSave.ts
// Saving one change to several clips (a selection's speed or volume): a few at
// a time — each re-renders a voice on the server — and a failure never loses
// the clips that did save, so they still get their undo step. Pure.

export async function saveEach<T>(
  ids: string[],
  save: (id: string) => Promise<T>,
  atOnce = 4,
): Promise<{ ok: T[]; failed: string[] }> {
  const ok: T[] = []
  const failed: string[] = []
  for (let i = 0; i < ids.length; i += atOnce) {
    const batch = ids.slice(i, i + atOnce)
    const results = await Promise.allSettled(batch.map((id) => save(id)))
    results.forEach((r, k) => {
      if (r.status === 'fulfilled') ok.push(r.value)
      else failed.push(batch[k])
    })
  }
  return { ok, failed }
}
