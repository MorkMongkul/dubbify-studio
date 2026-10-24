// src/lib/playbackSync.ts
// Keeping the preview's sound (hooks/usePreviewAudio, its own clock) with the
// picture. WebKit — Safari and the desktop app — stalls a playing media element
// whenever it is touched: a seek stalls it ~0.3–1 s, and a change of speed
// stops a video outright while it lasts (12 changes in 6 s: it moved 0.18 s;
// measured 2026-10-24). So the picture is never touched while it plays: when
// it and the sound drift further apart than the eye forgives, the sound, which
// starts again instantly anywhere, starts again from where the picture is. Pure.

/** The sound may be heard this far ahead of the picture (s)… */
const EARLY = 0.045
/** …or this far behind it, before anyone notices (ITU-R BT.1359). */
const LATE = 0.125
/** After a start, ms the picture gets to settle before it's judged. */
export const SETTLE_MS = 600

export type SyncAction = 'none' | 'resync'

/** `drift`: the picture's position minus the sound being heard (s; positive = the sound is late). */
export function syncAction(drift: number, msSinceStart: number): SyncAction {
  if (msSinceStart < SETTLE_MS) return 'none'
  return drift > LATE || drift < -EARLY ? 'resync' : 'none'
}
