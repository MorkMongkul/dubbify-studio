// The Media tab's clips as the editor makes and edits them (src/lib/media.ts) —
// the same rules the server keeps (backend/app/services/media.py).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  addAt, defaultClip, drawOrder, duplicateClip, fitBox, mediaExportWarnings, mediaRows, pasteClips, splitClip, trimMediaEnd,
  trimMediaStart,
} from '../src/lib/media.ts'
import type { MediaAsset, MediaClip } from '../src/types/index.ts'

const asset = (fields: Partial<MediaAsset> = {}) => ({
  id: 'v', project_id: 'p', name: 'sponsor.mov', kind: 'video', width: 1080, height: 1920, duration_secs: 7,
  has_alpha: true, alpha_kept: true, has_audio: false, last_box: null, status: 'ready', error: null,
  created_at: null, url: null, preview_url: null, thumb_url: null, audio_url: null, ...fields,
}) as MediaAsset
const VIDEO = asset()
const IMAGE = asset({ id: 'i', kind: 'image', duration_secs: null, width: 1000, height: 1000 })
const FRAME = { width: 720, height: 1280 }

let n = 0
const clip = (start: number, end: number, lane = 0, fields: Partial<MediaClip> = {}): MediaClip => ({
  id: `c${n++}`, asset_id: 'v', lane_index: lane, start_time: start, end_time: end, source_in: 0,
  x: 0, y: 0, width: 1, height: 1, volume_db: 0, ...fields,
})

test('a dropped clip: a video its own length, an image 5 s, both cut at the episode end', () => {
  const v = defaultClip(VIDEO, 2, 88, 0, FRAME)
  assert.deepEqual([v.start_time, v.end_time, v.source_in, v.lane_index], [2, 9, 0, 0])
  assert.equal(defaultClip(VIDEO, 85, 88, 0, FRAME).end_time, 88)
  const i = defaultClip(IMAGE, 10, 88, 1, FRAME)
  assert.deepEqual([i.start_time, i.end_time, i.lane_index], [10, 15, 1])
})

test('a new clip fits the frame — the sponsor card, the episodes\' own shape, fills it — or goes where its media was last', () => {
  assert.deepEqual(fitBox(1080, 1920, 720, 1280), { x: 0, y: 0, width: 1, height: 1 })
  assert.deepEqual(fitBox(1000, 1000, 720, 1280), { x: 0, y: 0.21875, width: 1, height: 0.5625 })
  const placed = defaultClip(asset({ last_box: { x: 0.1, y: 0.7, width: 0.5, height: 0.2 } }), 0, 88, 0, FRAME)
  assert.deepEqual([placed.x, placed.y, placed.width, placed.height], [0.1, 0.7, 0.5, 0.2])
})

test('a video can be shortened but never stretched past its own end; an image stretches', () => {
  const v = clip(2, 9)
  assert.equal(trimMediaEnd(v, VIDEO, 12, 88, []).end_time, 9)
  assert.equal(trimMediaEnd(v, VIDEO, 6, 88, []).end_time, 6)
  const i = clip(2, 7, 0, { asset_id: 'i' })
  assert.equal(trimMediaEnd(i, IMAGE, 40, 88, []).end_time, 40)
  assert.equal(trimMediaEnd(i, IMAGE, 100, 88, []).end_time, 88)
  // never over the next clip on its track
  assert.equal(trimMediaEnd(i, IMAGE, 40, 88, [clip(20, 25)]).end_time, 20)
})

test('dragging a video\'s left edge uncovers its start no further than its first frame', () => {
  const v = clip(2, 9, 0, { source_in: 0.5 })
  assert.deepEqual(trimMediaStart(v, VIDEO, 1, []), { start_time: 1.5, source_in: 0 })
  assert.deepEqual(trimMediaStart(v, VIDEO, 3, []), { start_time: 3, source_in: 1.5 })
  // never over the clip before it on its track
  assert.deepEqual(trimMediaStart(clip(5, 9, 0, { asset_id: 'i' }), IMAGE, 1, [clip(0, 3)]), { start_time: 3, source_in: 0 })
})

test('paste keeps the copies\' spacing, boxes and volume, on the top free spot', () => {
  const copied = [clip(10, 15, 0, { x: 0.2, volume_db: -3 }), clip(17, 20, 0)]
  const pasted = pasteClips(copied, 30, [])
  assert.deepEqual(pasted.map((c) => [c.start_time, c.end_time, c.lane_index]), [[30, 35, 0], [37, 40, 0]])
  assert.deepEqual([pasted[0].x, pasted[0].volume_db], [0.2, -3])
  assert.ok(pasted.every((c) => c.id && !copied.some((o) => o.id === c.id)))
  // a busy track: a new track above everything
  const busy = pasteClips([clip(10, 15)], 30, [clip(31, 33, 0), clip(0, 2, 2)])
  assert.equal(busy[0].lane_index, 3)
})

test('a duplicate goes right after its clip, else on a new track above', () => {
  const c = clip(2, 9)
  const d = duplicateClip(c, [c])
  assert.deepEqual([d.start_time, d.end_time, d.lane_index], [9, 16, 0])
  const blocked = duplicateClip(c, [c, clip(10, 12, 0)])
  assert.deepEqual([blocked.start_time, blocked.lane_index], [9, 1])
})

test('splitting keeps the video playing on: the right half starts where the left ended', () => {
  const [left, right] = splitClip(clip(2, 9, 0, { source_in: 0.5 }), 4, 'video')
  assert.deepEqual([left.start_time, left.end_time, left.source_in], [2, 4, 0.5])
  assert.deepEqual([right.start_time, right.end_time, right.source_in], [4, 9, 2.5])
  const [, img] = splitClip(clip(2, 9, 0, { asset_id: 'i' }), 4, 'image')
  assert.equal(img.source_in, 0)
})

test('tracks are drawn top lane first; layers paint lower lanes first, hidden tracks left out', () => {
  const clips = [clip(0, 1, 0), clip(0, 1, 2), clip(3, 4, 0), clip(0, 1, 1)]
  assert.deepEqual(mediaRows(clips), [2, 1, 0])
  const flags = { 1: { locked: false, hidden: true, muted: false } }
  assert.deepEqual(drawOrder(clips, flags).map((c) => [c.lane_index, c.start_time]), [[0, 0], [0, 3], [2, 0]])
})

test('the export window names media tracks that are hidden or muted', () => {
  const flags = { 2: { locked: false, hidden: true, muted: false }, 1: { locked: false, hidden: false, muted: true } }
  assert.deepEqual(mediaExportWarnings([2, 1, 0], flags), [
    "Media track 1 is hidden: it won't be in the export",
    "Media track 2 is muted: its sound won't be in the export",
  ])
})

test('+ adds at the playhead on the top track that is free there, else on a new track above', () => {
  assert.equal(addAt(clip(10, 15, 0), []).lane_index, 0)
  const existing = [clip(0, 20, 0), clip(30, 40, 1)]
  assert.equal(addAt(clip(10, 15, 0), existing).lane_index, 1)
  assert.equal(addAt(clip(32, 35, 0), existing).lane_index, 0)
  assert.equal(addAt(clip(12, 14, 0), [clip(0, 20, 0), clip(10, 20, 1)]).lane_index, 2)
  // never onto a locked track
  assert.equal(addAt(clip(10, 15, 0), existing, new Set([1])).lane_index, 2)
})

