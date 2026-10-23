// The music & effects and the original voices in pieces (0.3.2) — the same
// rules as backend/app/services/stem_pieces.py. A piece is its stem heard from
// start to end at the same time as in the video: locked to the picture, so
// nothing here ever moves one; between pieces, silence. Every function returns
// a new list. Pure logic: `npm test` runs it.
import type { StemPiece } from '../types/index.ts'

export const MIN_PIECE_SECS = 0.1

const r3 = (x: number) => Math.round(x * 1000) / 1000
const byStart = (a: StemPiece, b: StemPiece) => a.start_time - b.start_time

/** An id for a new piece (the server keeps the ids it's sent). */
export function newPieceId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `p-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

/** The piece playing at `t`, or null in a gap. */
export function pieceAt(pieces: StemPiece[], t: number): StemPiece | null {
  return pieces.find((p) => p.start_time <= t && t < p.end_time) ?? null
}

/** A stem's gain at `t`: the piece's volume × its linear fades; 0 between pieces. */
export function pieceGainAt(pieces: StemPiece[], t: number): number {
  const p = pieceAt(pieces, t)
  if (!p) return 0
  let shape = 1
  if (p.fade_in_secs > 0) shape = Math.min(shape, (t - p.start_time) / p.fade_in_secs)
  if (p.fade_out_secs > 0) shape = Math.min(shape, (p.end_time - t) / p.fade_out_secs)
  return 10 ** ((p.volume_db ?? 0) / 20) * Math.max(0, shape)
}

/** Fades no longer, together, than the piece. */
function fitFades(p: StemPiece): StemPiece {
  const length = p.end_time - p.start_time
  const sum = p.fade_in_secs + p.fade_out_secs
  if (sum <= length + 1e-9) return p
  const k = length / sum
  return { ...p, fade_in_secs: r3(p.fade_in_secs * k), fade_out_secs: r3(p.fade_out_secs * k) }
}

/** Cut a piece in two at `at`; each half keeps its outer fade. Too close to an edge: unchanged. */
export function splitPiece(pieces: StemPiece[], id: string, at: number, newId: string): StemPiece[] {
  const p = pieces.find((x) => x.id === id)
  if (!p || at <= p.start_time + MIN_PIECE_SECS || at >= p.end_time - MIN_PIECE_SECS) return pieces
  const first = fitFades({ ...p, end_time: r3(at), fade_out_secs: 0 })
  const second = fitFades({ ...p, id: newId, start_time: r3(at), fade_in_secs: 0 })
  return pieces.flatMap((x) => (x.id === id ? [first, second] : [x])).sort(byStart)
}

/** Drag an edge: it cuts sound, or brings it back up to the next piece (or 0, or the video's end). */
export function trimPiece(pieces: StemPiece[], id: string, edge: 'start' | 'end', t: number, duration: number): StemPiece[] {
  const sorted = [...pieces].sort(byStart)
  const i = sorted.findIndex((x) => x.id === id)
  if (i < 0) return pieces
  const p = sorted[i]
  let next: StemPiece
  if (edge === 'start') {
    const lo = i > 0 ? sorted[i - 1].end_time : 0
    next = { ...p, start_time: r3(Math.max(lo, Math.min(p.end_time - MIN_PIECE_SECS, t))) }
  } else {
    const hi = i < sorted.length - 1 ? sorted[i + 1].start_time : duration
    next = { ...p, end_time: r3(Math.min(hi, Math.max(p.start_time + MIN_PIECE_SECS, t))) }
  }
  return sorted.map((x) => (x.id === id ? fitFades(next) : x))
}

/** Delete a piece: silence there. */
export function deletePiece(pieces: StemPiece[], id: string): StemPiece[] {
  return pieces.filter((x) => x.id !== id)
}

/** Change one piece's settings (volume, fades, in the export). */
export function setPiece(pieces: StemPiece[], id: string, fields: Partial<Pick<StemPiece, 'volume_db' | 'fade_in_secs' | 'fade_out_secs' | 'in_export'>>): StemPiece[] {
  return pieces.map((x) => (x.id === id ? fitFades({ ...x, ...fields }) : x))
}
