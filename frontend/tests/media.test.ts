// The Media tab's clips as the editor makes and edits them (src/lib/media.ts) —
// the same rules the server keeps (backend/app/services/media.py).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  addAt, defaultClip, drawOrder, dropNewClip, duplicateClip, fitBox, mediaExportWarnings, mediaRows, mediaVideoStep, mediaWindow,
  pasteClips, planMediaDrop, resizeBox, splitClip, trimMediaEnd, trimMediaStart,
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


const LOCK = { locked: true, hidden: false, muted: false }
const lanesOf = (clips: MediaClip[]) => Object.fromEntries(clips.map((c) => [c.id, [c.lane_index, c.start_time, c.end_time]]))

test('a dragged clip moves in time on its own track, never before 0 or past the episode end', () => {
  const a = clip(2, 9, 0, { id: 'a' })
  assert.deepEqual(lanesOf(planMediaDrop([a], ['a'], 'a', 3, { kind: 'onto', row: 0 }, {}, 88).clips), { a: [0, 5, 12] })
  assert.deepEqual(lanesOf(planMediaDrop([a], ['a'], 'a', -10, { kind: 'onto', row: 0 }, {}, 88).clips), { a: [0, 0, 7] })
  assert.deepEqual(lanesOf(planMediaDrop([a], ['a'], 'a', 90, { kind: 'onto', row: 0 }, {}, 88).clips), { a: [0, 81, 88] })
})

test('a clip dropped onto a free track goes there; a track it leaves empty vanishes and the rest close up', () => {
  // rows from the top: lane 2 (a), lane 1 (b), lane 0 (c)
  const clips = [clip(0, 5, 2, { id: 'a' }), clip(0, 5, 1, { id: 'b' }), clip(10, 15, 0, { id: 'c' })]
  const out = planMediaDrop(clips, ['b'], 'b', 0, { kind: 'onto', row: 2 }, {}, 88)
  assert.deepEqual(lanesOf(out.clips), { a: [1, 0, 5], b: [0, 0, 5], c: [0, 10, 15] })
})

test('a drop onto a busy or locked track makes a new track under it; switches stay with their tracks', () => {
  const clips = [clip(0, 5, 2, { id: 'a' }), clip(0, 5, 1, { id: 'b' }), clip(0, 5, 0, { id: 'c' })]
  // c onto a's busy track (row 0): a new track between a and b
  const busy = planMediaDrop(clips, ['c'], 'c', 0, { kind: 'onto', row: 0 }, {}, 88)
  assert.deepEqual(lanesOf(busy.clips), { a: [2, 0, 5], c: [1, 0, 5], b: [0, 0, 5] })
  assert.deepEqual(busy.track_flags, {})
  // c to a free time on a's locked track: refused, a new track under it
  const locked = planMediaDrop(clips, ['c'], 'c', 20, { kind: 'onto', row: 0 }, { 2: LOCK }, 88)
  assert.deepEqual(lanesOf(locked.clips), { a: [2, 0, 5], c: [1, 20, 25], b: [0, 0, 5] })
  assert.deepEqual(locked.track_flags, { 2: LOCK })
})

test('a drop between tracks inserts one there; the locked track keeps its switch as it moves down', () => {
  const clips = [clip(0, 5, 2, { id: 'a' }), clip(0, 5, 1, { id: 'b' }), clip(0, 5, 0, { id: 'c' })]
  const out = planMediaDrop(clips, ['c'], 'c', 0, { kind: 'insert', boundary: 0 }, { 2: LOCK }, 88)
  assert.deepEqual(lanesOf(out.clips), { c: [2, 0, 5], a: [1, 0, 5], b: [0, 0, 5] })
  assert.deepEqual(out.track_flags, { 1: LOCK })
})

test('a tile dropped on the timeline: onto a free track, else a new track above the one it was dropped on', () => {
  const clips = [clip(0, 5, 1, { id: 'a' }), clip(0, 5, 0, { id: 'b' })]
  const tile = clip(10, 15, 0, { id: 'new' })
  assert.deepEqual(lanesOf(dropNewClip(clips, tile, { kind: 'onto', row: 1 }, {}, 88).clips).new, [0, 10, 15])
  const onBusy = dropNewClip(clips, clip(2, 7, 0, { id: 'new' }), { kind: 'onto', row: 1 }, { 0: LOCK }, 88)
  assert.deepEqual(lanesOf(onBusy.clips), { a: [2, 0, 5], new: [1, 2, 7], b: [0, 0, 5] })
  assert.deepEqual(onBusy.track_flags, { 0: LOCK })
  // the first media clip of an episode
  assert.deepEqual(lanesOf(dropNewClip([], tile, { kind: 'insert', boundary: 0 }, {}, 88).clips), { new: [0, 10, 15] })
})

test('a paste never lands on a locked track', () => {
  const pasted = pasteClips([clip(10, 15, 0)], 30, [clip(0, 2, 0)], new Set([0]))
  assert.equal(pasted[0].lane_index, 1)
})

test('a video clip gets its player 2 s before it starts, until it ends', () => {
  const clips = [clip(10, 17, 0, { id: 'v' }), clip(30, 35, 0, { id: 'i', asset_id: 'i' })]
  const kinds = new Map([['v', 'video'], ['i', 'image']]) as Map<string, 'video' | 'image'>
  assert.deepEqual(mediaWindow(clips, kinds, 7.9), [])
  assert.deepEqual(mediaWindow(clips, kinds, 8), ['v'])
  assert.deepEqual(mediaWindow(clips, kinds, 16.9), ['v'])
  assert.deepEqual(mediaWindow(clips, kinds, 17), [])
  assert.deepEqual(mediaWindow(clips, kinds, 31), [])
})

test("a clip's player waits paused on its first frame and starts early by the picture's start delay", () => {
  const c = clip(10, 17, 0, { source_in: 0.5 })
  const run = { lead: 0.5, sincePlayMs: 5000, refitted: false }
  // playing, the clip ahead: parked on its first frame
  assert.deepEqual(mediaVideoStep(c, 9, true, { paused: true, currentTime: 0 }, run), { kind: 'pause', seek: 0.5 })
  assert.deepEqual(mediaVideoStep(c, 9.4, true, { paused: true, currentTime: 0.5 }, run), { kind: 'none' })
  // started the start delay early, so its first frame shows as the clip starts
  assert.deepEqual(mediaVideoStep(c, 9.5, true, { paused: true, currentTime: 0.5 }, run), { kind: 'play', seek: null })
  // joined late (play pressed inside the clip): ahead by the delay
  assert.deepEqual(mediaVideoStep(c, 12, true, { paused: true, currentTime: 0.5 }, run), { kind: 'play', seek: 3 })
  // the clip ends: paused
  assert.deepEqual(mediaVideoStep(c, 17, true, { paused: false, currentTime: 7.5 }, run), { kind: 'pause', seek: null })
  // stopped: the exact frame
  assert.deepEqual(mediaVideoStep(c, 12, false, { paused: false, currentTime: 2.6 }, run), { kind: 'pause', seek: 2.5 })
  assert.deepEqual(mediaVideoStep(c, 12, false, { paused: true, currentTime: 2.5 }, run), { kind: 'none' })
})

test("a playing clip's player is judged once it has settled, and put back at most once per start", () => {
  const c = clip(10, 17, 0, { source_in: 0.5 })
  const playing = { paused: false, currentTime: 2.9 }        // 0.4 s off at 12 s
  // a seek stalls a playing video: a little off is left alone
  assert.deepEqual(mediaVideoStep(c, 12, true, { paused: false, currentTime: 2.7 }, { lead: 0, sincePlayMs: 5000, refitted: false }), { kind: 'none' })
  // right after a start WebKit's position runs ahead of its frames: not judged yet
  assert.deepEqual(mediaVideoStep(c, 12, true, playing, { lead: 0, sincePlayMs: 300, refitted: false }), { kind: 'none' })
  // settled and off: put back, ahead by the start delay it really had — started 0.5 s early
  // and still 0.4 s ahead, it took 0.1 s to start
  assert.deepEqual(mediaVideoStep(c, 12, true, playing, { lead: 0.5, sincePlayMs: 5000, refitted: false }), { kind: 'refit', seek: 2.6 })
  // started on time and 0.4 s behind: it took 0.4 s
  assert.deepEqual(mediaVideoStep(c, 12, true, { paused: false, currentTime: 2.1 }, { lead: 0, sincePlayMs: 5000, refitted: false }), { kind: 'refit', seek: 2.9 })
  // never again until it starts again (a steady start delay would make it loop)
  assert.deepEqual(mediaVideoStep(c, 12, true, playing, { lead: 0.5, sincePlayMs: 5000, refitted: true }), { kind: 'none' })
})

test('a box dragged by a corner keeps its shape; the opposite corner stays put', () => {
  const box = { x: 0.2, y: 0.2, width: 0.4, height: 0.2 }
  // bottom-right outwards by 0.1 of the frame's width (the width leads)
  const br = resizeBox(box, 'se', 0.1)
  assert.deepEqual(br, { x: 0.2, y: 0.2, width: 0.5, height: 0.25 })
  // top-left inwards: the bottom-right corner stays
  const tl = resizeBox(box, 'nw', 0.2)
  assert.deepEqual(tl, { x: 0.4, y: 0.3, width: 0.2, height: 0.1 })
  // never smaller than a sliver
  assert.ok(resizeBox(box, 'se', -1).height >= 0.02)
})

test("a dragged clip's track keeps its switches when the clip is alone on it", () => {
  const hidden = { locked: false, hidden: true, muted: true }
  const card = clip(2, 9, 0, { id: 'card' })
  const out = planMediaDrop([card], ['card'], 'card', 2, { kind: 'onto', row: 0 }, { 0: hidden }, 88)
  assert.deepEqual(out.track_flags, { 0: hidden })
  // a whole track moved above another keeps its switches; the track it left had nothing else
  const both = [clip(0, 5, 1, { id: 'a' }), clip(0, 5, 0, { id: 'b' })]
  const up = planMediaDrop(both, ['b'], 'b', 0, { kind: 'insert', boundary: 0 }, { 0: hidden }, 88)
  assert.deepEqual(lanesOf(up.clips), { b: [1, 0, 5], a: [0, 0, 5] })
  assert.deepEqual(up.track_flags, { 1: hidden })
  // a clip taken from a track that keeps other clips leaves the switches with that track
  const shared = [clip(0, 5, 0, { id: 'c' }), clip(10, 15, 0, { id: 'd' })]
  const out2 = planMediaDrop(shared, ['d'], 'd', 0, { kind: 'insert', boundary: 0 }, { 0: hidden }, 88)
  assert.deepEqual(lanesOf(out2.clips), { c: [0, 0, 5], d: [1, 10, 15] })
  assert.deepEqual(out2.track_flags, { 0: hidden })
})

test('clips get ids where the browser has no crypto.randomUUID (Safari before 15.4)', () => {
  const had = Object.getOwnPropertyDescriptor(globalThis.crypto, 'randomUUID')
  Object.defineProperty(globalThis.crypto, 'randomUUID', { value: undefined, configurable: true })
  try {
    const a = defaultClip(VIDEO, 0, 88, 0, FRAME)
    const b = duplicateClip(a, [a])
    assert.ok(a.id && b.id && a.id !== b.id && a.id.length <= 36)
  } finally {
    if (had) Object.defineProperty(globalThis.crypto, 'randomUUID', had)
    else delete (globalThis.crypto as { randomUUID?: unknown }).randomUUID
  }
})
