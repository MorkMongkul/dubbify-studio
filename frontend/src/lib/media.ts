// src/lib/media.ts
// The Media tab's clips (0.4.0), as the editor makes and edits them. The
// editor sends an episode's whole list and the server keeps it sound with the
// same rules (backend/app/services/media.py). A higher lane_index is drawn over
// a lower one, so clips never change lane by themselves: a copy that can't go
// on its own track goes on a new track above everything. Pure: `npm test` runs it.
import type { MediaAsset, MediaBox, MediaClip, MediaTrackFlagMap, MediaTrackSwitches } from '../types/index.ts'
import { planDrop, type DropSpot } from './tracks.ts'

/** The drag type a Media tab tile carries (its asset id) onto the timeline. */
export const MEDIA_DRAG = 'application/x-movelo-media'
export const IMAGE_SECS = 5
export const MIN_CLIP_SECS = 0.1
export const MAX_EXPORT_CLIPS = 32
export const VOLUME_MIN_DB = -20
export const VOLUME_MAX_DB = 10

const EPS = 1e-6
const round6 = (x: number) => Math.round(x * 1e6) / 1e6
const newId = () => crypto.randomUUID()

/** A media asset scaled to fit the frame (contain), centred — fractions of the frame. */
export function fitBox(w: number, h: number, frameW: number, frameH: number): MediaBox {
  if (!w || !h || !frameW || !frameH) return { x: 0, y: 0, width: 1, height: 1 }
  const scale = Math.min(frameW / w, frameH / h)
  const width = round6((w * scale) / frameW)
  const height = round6((h * scale) / frameH)
  return { x: round6((1 - width) / 2), y: round6((1 - height) / 2), width, height }
}

/**
 * A clip of `asset` dropped at `at` on `lane`: a video as long as itself, an
 * image IMAGE_SECS, both cut at the episode's end; where the asset was last
 * placed, else fitted to the frame.
 */
export function defaultClip(
  asset: MediaAsset, at: number, episodeSecs: number, lane: number, frame: { width: number; height: number },
): MediaClip {
  const length = asset.kind === 'video' ? asset.duration_secs ?? IMAGE_SECS : IMAGE_SECS
  const start = Math.max(0, at)
  const end = episodeSecs > 0 ? Math.min(episodeSecs, start + length) : start + length
  const box = asset.last_box ?? fitBox(asset.width, asset.height, frame.width, frame.height)
  return {
    id: newId(), asset_id: asset.id, lane_index: lane, start_time: round6(start), end_time: round6(end),
    source_in: 0, volume_db: 0, ...box,
  }
}

const onLane = (c: MediaClip, lane: number, others: MediaClip[]) =>
  others.filter((o) => o.id !== c.id && o.lane_index === lane)

/** Drag a clip's left edge to `t`: a video cuts or uncovers its start, never before its first frame; never over the clip before it. */
export function trimMediaStart(
  clip: MediaClip, asset: Pick<MediaAsset, 'kind'>, t: number, others: MediaClip[],
): { start_time: number; source_in: number } {
  const prevEnd = Math.max(0, ...onLane(clip, clip.lane_index, others)
    .filter((o) => o.end_time <= clip.start_time + EPS).map((o) => o.end_time))
  let start = Math.min(Math.max(t, prevEnd), clip.end_time - MIN_CLIP_SECS)
  if (asset.kind !== 'video') return { start_time: round6(start), source_in: 0 }
  start = Math.max(start, clip.start_time - clip.source_in)
  return { start_time: round6(start), source_in: round6(clip.source_in + (start - clip.start_time)) }
}

/** Drag a clip's right edge to `t`: a video never past its own end, an image to the episode's end; never over the next clip. */
export function trimMediaEnd(
  clip: MediaClip, asset: Pick<MediaAsset, 'kind' | 'duration_secs'>, t: number, episodeSecs: number, others: MediaClip[],
): { end_time: number } {
  const nextStart = Math.min(Infinity, ...onLane(clip, clip.lane_index, others)
    .filter((o) => o.start_time >= clip.end_time - EPS).map((o) => o.start_time))
  let end = Math.max(t, clip.start_time + MIN_CLIP_SECS)
  if (asset.kind === 'video') end = Math.min(end, clip.start_time + ((asset.duration_secs ?? 0) - clip.source_in))
  if (episodeSecs > 0) end = Math.min(end, episodeSecs)
  return { end_time: round6(Math.min(end, nextStart)) }
}

const free = (lane: number, start: number, end: number, placed: MediaClip[]) =>
  placed.every((c) => c.lane_index !== lane || c.end_time <= start + EPS || c.start_time >= end - EPS)

/** On `lane` when free over its span (and not locked), else on a new track above every track there is. */
function place(c: MediaClip, placed: MediaClip[], locked: Set<number> = new Set()): MediaClip {
  if (!locked.has(c.lane_index) && free(c.lane_index, c.start_time, c.end_time, placed)) return c
  return { ...c, lane_index: Math.max(c.lane_index, ...placed.map((p) => p.lane_index)) + 1 }
}

/** A new clip (the tile's +, at the playhead): on the top unlocked track that is free over its span, else a new track above. */
export function addAt(clip: MediaClip, existing: MediaClip[], locked: Set<number> = new Set()): MediaClip {
  const lanes = mediaRows(existing)
  const lane = lanes.find((l) => !locked.has(l) && free(l, clip.start_time, clip.end_time, existing))
  return { ...clip, lane_index: lane ?? (lanes.length ? lanes[0] + 1 : 0) }
}

/** Copied clips pasted at `at`: the same spacing, boxes and volume; each on its own track when free there, else above. */
export function pasteClips(copied: MediaClip[], at: number, existing: MediaClip[], locked: Set<number> = new Set()): MediaClip[] {
  if (!copied.length) return []
  const first = Math.min(...copied.map((c) => c.start_time))
  const placed = [...existing]
  const made: MediaClip[] = []
  for (const c of [...copied].sort((a, b) => a.start_time - b.start_time)) {
    const shift = at - first
    const moved = place({ ...c, id: newId(), start_time: round6(c.start_time + shift), end_time: round6(c.end_time + shift) }, placed, locked)
    placed.push(moved)
    made.push(moved)
  }
  return made
}

/** A copy right after the clip on its track when free, else on a new track above. */
export function duplicateClip(clip: MediaClip, existing: MediaClip[]): MediaClip {
  const length = clip.end_time - clip.start_time
  return place({ ...clip, id: newId(), start_time: clip.end_time, end_time: round6(clip.end_time + length) }, existing)
}

/** A clip cut in two at `at`: a video's right half plays on from where the left half ended. */
export function splitClip(clip: MediaClip, at: number, kind: MediaAsset['kind']): [MediaClip, MediaClip] {
  const left = { ...clip, end_time: round6(at) }
  const right = {
    ...clip, id: newId(), start_time: round6(at),
    source_in: kind === 'video' ? round6(clip.source_in + (at - clip.start_time)) : 0,
  }
  return [left, right]
}

/**
 * Clips dragged by `deltaSecs` (the group stays inside the episode) to `spot`,
 * a row counted from the top: the voice tracks' drop rules (lib/tracks.ts
 * planDrop — a busy or locked track, or a drop between tracks, makes a new
 * track under the target row), run with the top track as row 0. Then the
 * tracks are numbered again, the top one highest, so their order — what
 * covers what — never changes; a track's switches go with it (an emptied
 * track's are dropped).
 */
export function planMediaDrop(
  clips: MediaClip[], moving: string[], grabbedId: string, deltaSecs: number, spot: DropSpot,
  flags: MediaTrackFlagMap | null | undefined, episodeSecs: number,
): { clips: MediaClip[]; track_flags: MediaTrackFlagMap } {
  const ids = new Set(moving)
  const rows = mediaRows(clips)
  const rowOf = new Map(rows.map((lane, i) => [lane, i]))
  const movers = clips.filter((c) => ids.has(c.id))
  let delta = deltaSecs
  if (episodeSecs > 0 && movers.length) delta = Math.min(delta, episodeSecs - Math.max(...movers.map((c) => c.end_time)))
  const locked = new Set(rows.flatMap((lane, i) => (mediaSwitchesOf(flags, lane).locked ? [i] : [])))
  const placed = clips.map((c) => ({ id: c.id, start_time: c.start_time, end_time: c.end_time, lane_index: rowOf.get(c.lane_index)! }))
  const changes = new Map(planDrop(placed, moving, grabbedId, delta, spot, locked).map((ch) => [ch.id, ch]))
  const rowAfter = (c: MediaClip) => changes.get(c.id)?.lane_index ?? rowOf.get(c.lane_index)!
  const used = [...new Set(clips.map(rowAfter))].sort((a, b) => a - b)
  const laneFor = new Map(used.map((row, i) => [row, used.length - 1 - i]))
  const track_flags: MediaTrackFlagMap = {}
  for (const c of clips) {
    const f = flags?.[String(c.lane_index)]
    if (f && !ids.has(c.id)) track_flags[String(laneFor.get(rowAfter(c))!)] = f
  }
  return {
    clips: clips.map((c) => {
      const ch = changes.get(c.id)
      return { ...c, start_time: ch?.start_time ?? c.start_time, end_time: ch?.end_time ?? c.end_time, lane_index: laneFor.get(rowAfter(c))! }
    }),
    track_flags,
  }
}

/** A tile dropped on the timeline: onto a free, unlocked track, else on a new track above the one it was dropped on. */
export function dropNewClip(
  clips: MediaClip[], clip: MediaClip, spot: DropSpot, flags: MediaTrackFlagMap | null | undefined, episodeSecs: number,
): { clips: MediaClip[]; track_flags: MediaTrackFlagMap } {
  const rows = mediaRows(clips)
  if (!rows.length) return { clips: [{ ...clip, lane_index: 0 }], track_flags: {} }
  if (spot.kind === 'onto') {
    const lane = rows[Math.min(spot.row, rows.length - 1)]
    if (!mediaSwitchesOf(flags, lane).locked && free(lane, clip.start_time, clip.end_time, clips)) {
      return { clips: [...clips, { ...clip, lane_index: lane }], track_flags: { ...(flags ?? {}) } }
    }
  }
  const boundary = spot.kind === 'insert' ? spot.boundary : spot.row
  return planMediaDrop([...clips, { ...clip, lane_index: rows[0] }], [clip.id], clip.id, 0, { kind: 'insert', boundary }, flags, episodeSecs)
}

/** The media tracks to draw, the top one (highest lane) first. */
export function mediaRows(clips: Pick<MediaClip, 'lane_index'>[]): number[] {
  return [...new Set(clips.map((c) => c.lane_index))].sort((a, b) => b - a)
}

const OFF: MediaTrackSwitches = { locked: false, hidden: false, muted: false }

export function mediaSwitchesOf(map: MediaTrackFlagMap | null | undefined, lane: number): MediaTrackSwitches {
  return { ...OFF, ...(map?.[String(lane)] ?? {}) }
}

/** A media track's switch turned on or off; only tracks with a switch on are kept. */
export function withMediaSwitch(
  map: MediaTrackFlagMap | null | undefined, lane: number, key: keyof MediaTrackSwitches, on: boolean,
): MediaTrackFlagMap {
  const next: MediaTrackFlagMap = { ...(map ?? {}) }
  const f = { ...mediaSwitchesOf(map, lane), [key]: on }
  if (f.locked || f.hidden || f.muted) next[String(lane)] = f
  else delete next[String(lane)]
  return next
}

/** The clips to paint, lower tracks first (a higher track covers a lower one), hidden tracks left out. */
export function drawOrder(clips: MediaClip[], flags: MediaTrackFlagMap | null | undefined): MediaClip[] {
  return clips
    .filter((c) => !mediaSwitchesOf(flags, c.lane_index).hidden)
    .sort((a, b) => a.lane_index - b.lane_index || a.start_time - b.start_time)
}

/** What the export window says it leaves out ("Media track N" counts the drawn rows from the top). */
export function mediaExportWarnings(rows: number[], flags: MediaTrackFlagMap | null | undefined): string[] {
  const out: string[] = []
  rows.forEach((lane, i) => {
    const { hidden, muted } = mediaSwitchesOf(flags, lane)
    if (hidden) out.push(`Media track ${i + 1} is hidden: it won't be in the export`)
    else if (muted) out.push(`Media track ${i + 1} is muted: its sound won't be in the export`)
  })
  return out
}
