// src/lib/playbackSync.ts
// Keeping an audio player (the music, the original voices, a voice clip) with
// the video without cutting its sound. Re-seeking on every small drift cut the
// sound again and again: a seek takes WebKit ~0.3 s to resume, which put the
// player behind again, which triggered another seek (measured on a real
// episode: 10–18 seeks per player in 8 s of playback). Now a small drift is
// caught up by playing up to 5% faster or slower (pitch kept — inaudible), and
// only a big one jumps, after which the player gets time to settle. Pure.

/** Drift (s) below which a player counts as in step. */
const IN_STEP = 0.04
/** Drift (s) beyond which the player jumps (a seek elsewhere, a stall). */
const JUMP = 0.5
/** After a jump or a start, ms the player gets to settle before it's judged. */
const SETTLE_MS = 600
/** The most a nudge changes the player's speed. */
const MAX_NUDGE = 0.05

export type SyncAction = { kind: 'none' } | { kind: 'nudge'; factor: number } | { kind: 'jump' }

/** `drift`: the player's position minus where it should be (s; positive = ahead). */
export function syncAction(drift: number, msSinceJump: number): SyncAction {
  if (msSinceJump < SETTLE_MS) return { kind: 'none' }
  if (Math.abs(drift) > JUMP) return { kind: 'jump' }
  if (Math.abs(drift) < IN_STEP) return { kind: 'none' }
  // Catch up over a few seconds: behind → a little faster, ahead → a little slower
  return { kind: 'nudge', factor: 1 - Math.max(-MAX_NUDGE, Math.min(MAX_NUDGE, drift * 0.5)) }
}
