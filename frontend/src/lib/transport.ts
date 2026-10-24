// Keyboard transport for the editor: J / K / L, frame steps, clip edges. Pure logic: `npm test` runs it.

/** Keys typed into a text box (or a slider, a list) are never editor shortcuts. */
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as { tagName?: string; isContentEditable?: boolean } | null
  if (!el) return false
  const tag = (el.tagName ?? '').toUpperCase()
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!el.isContentEditable
}

/** One frame: the video's own frame rate, else 1/30 s. */
export function frameSecs(frameRate?: number | null): number {
  return frameRate && frameRate >= 10 && frameRate <= 120 ? 1 / frameRate : 1 / 30
}

/** The next (dir 1) or previous (dir −1) clip start or end from `t`. */
export function nextEdge(segs: { start_time: number; end_time: number }[], t: number, dir: 1 | -1): number | null {
  const edges = segs.flatMap((s) => [s.start_time, s.end_time])
  const ahead = dir === 1 ? edges.filter((e) => e > t + 1e-3) : edges.filter((e) => e < t - 1e-3)
  if (!ahead.length) return null
  return dir === 1 ? Math.min(...ahead) : Math.max(...ahead)
}

/** J / K / L: L plays forward (again: 2×, then 4×), J backward likewise, K stops. */
export function shuttle(rate: number, key: 'j' | 'k' | 'l'): number {
  if (key === 'k') return 0
  if (key === 'l') return rate <= 0 ? 1 : Math.min(4, rate * 2)
  return rate >= 0 ? -1 : Math.max(-4, rate * 2)
}
