// The music and the original voices in pieces (src/lib/stemPieces.ts) — the same rules as
// backend/app/services/stem_pieces.py. Pieces are locked to the picture: nothing here moves one.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { MIN_PIECE_SECS, deletePiece, pieceAt, pieceGainAt, setPiece, splitPiece, trimPiece } from '../src/lib/stemPieces.ts'
import type { StemPiece } from '../src/types/index.ts'

const p = (id: string, start: number, end: number, extra: Partial<StemPiece> = {}): StemPiece => ({
  id, stem: 'music', start_time: start, end_time: end, volume_db: 0, fade_in_secs: 0, fade_out_secs: 0, in_export: true, ...extra,
})
// The same table is in backend/app/tests/test_stem_pieces.py
const PIECE_TABLE: [number, number][] = [[0, 0.5012], [1, 0.5012], [1.75, 0.2506], [2, 0], [2.5, 0], [3, 0], [3.25, 0.5], [3.9, 1]]

test('the piece constants match the backend', () => {
  const py = readFileSync(new URL('../../backend/app/services/stem_pieces.py', import.meta.url), 'utf8')
  assert.match(py, new RegExp(`MIN_PIECE_SECS = ${MIN_PIECE_SECS}\\b`))
})

test('piece gain matches the shared table', () => {
  const pieces = [p('a', 0, 2, { volume_db: -6, fade_out_secs: 0.5 }), p('b', 3, 4, { fade_in_secs: 0.5 })]
  for (const [t, g] of PIECE_TABLE) assert.ok(Math.abs(pieceGainAt(pieces, t) - g) < 1e-4, `${t}: ${pieceGainAt(pieces, t)}`)
  assert.equal(pieceAt(pieces, 2.5), null)
  assert.equal(pieceAt(pieces, 3.5)?.id, 'b')
})

test('a split keeps each half its outer fade and never moves anything', () => {
  const out = splitPiece([p('a', 0, 4, { fade_in_secs: 0.5, fade_out_secs: 1, volume_db: -2 })], 'a', 1.5, 'new')
  assert.deepEqual(out.map((x) => [x.id, x.start_time, x.end_time, x.fade_in_secs, x.fade_out_secs, x.volume_db]),
    [['a', 0, 1.5, 0.5, 0, -2], ['new', 1.5, 4, 0, 1, -2]])
  // too close to an edge: nothing changes
  const same = [p('a', 0, 4)]
  assert.equal(splitPiece(same, 'a', 0.05, 'new'), same)
})

test('a trimmed edge can be dragged back out up to the next piece, never past it', () => {
  const pieces = [p('a', 0, 2), p('b', 3, 5), p('c', 6, 8)]
  assert.deepEqual(trimPiece(pieces, 'b', 'start', 1, 10).map((x) => [x.start_time, x.end_time]), [[0, 2], [2, 5], [6, 8]])
  assert.deepEqual(trimPiece(pieces, 'b', 'end', 7, 10)[1].end_time, 6)
  assert.deepEqual(trimPiece(pieces, 'b', 'end', 3.01, 10)[1].end_time, 3 + MIN_PIECE_SECS)
  assert.deepEqual(trimPiece(pieces, 'c', 'end', 20, 10)[2].end_time, 10)
  assert.deepEqual(trimPiece(pieces, 'a', 'start', -3, 10)[0].start_time, 0)
  // a trim shorter than the fades shrinks them
  const faded = trimPiece([p('a', 0, 4, { fade_in_secs: 1, fade_out_secs: 1 })], 'a', 'end', 1, 10)[0]
  assert.ok(faded.fade_in_secs + faded.fade_out_secs <= 1 + 1e-9)
})

test('deleting a piece leaves a gap; a setting changes one piece', () => {
  const pieces = [p('a', 0, 2), p('b', 3, 5)]
  assert.deepEqual(deletePiece(pieces, 'a').map((x) => x.id), ['b'])
  const set = setPiece(pieces, 'b', { in_export: false, volume_db: 4 })
  assert.deepEqual([set[1].in_export, set[1].volume_db, set[1].start_time], [false, 4, 3])
  assert.equal(set[0], pieces[0])
})
