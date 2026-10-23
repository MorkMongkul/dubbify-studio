// Tracks like CapCut's, for the editor's drops (the server settles anything
// else with the same rules: backend/app/services/timeline.py). A drop never
// moves a clip in time; empty tracks aren't shown. Pure logic: `npm test` runs it.

export type Placed = { id: string; start_time: number; end_time: number; lane_index?: number | null }
export type LayoutChange = { id: string; start_time?: number; end_time?: number; lane_index?: number; voice_offset?: number }
/** Onto an existing row, or a new track inserted at a boundary (0 = above the first row). */
export type DropSpot = { kind: 'onto'; row: number } | { kind: 'insert'; boundary: number }

const EPS = 0.001
const laneOf = (s: { lane_index?: number | null }) => s.lane_index ?? 0
const overlaps = (aStart: number, aEnd: number, bStart: number, bEnd: number) =>
  aStart < bEnd - EPS && bStart < aEnd - EPS

/** The tracks to draw: every lane that holds a clip, in order (empty ones vanish). */
export function trackRows(segs: { lane_index?: number | null }[]): number[] {
  const lanes = [...new Set(segs.map(laneOf))].sort((a, b) => a - b)
  return lanes.length ? lanes : [0]
}

/** Where a pointer at `y` (from the top of the first row) would drop. */
export function dropSpot(y: number, rowCount: number, rowHeight: number, edge = 8): DropSpot {
  const total = rowCount * rowHeight
  if (y < edge) return { kind: 'insert', boundary: 0 }
  if (y > total - edge) return { kind: 'insert', boundary: rowCount }
  const row = Math.min(rowCount - 1, Math.floor(y / rowHeight))
  const within = y - row * rowHeight
  if (within < edge && row > 0) return { kind: 'insert', boundary: row }
  if (within > rowHeight - edge && row < rowCount - 1) return { kind: 'insert', boundary: row + 1 }
  return { kind: 'onto', row }
}

export function fitsOnLane(segs: Placed[], lane: number, start: number, end: number, ignore: Set<string>): boolean {
  return !segs.some((s) => !ignore.has(s.id) && laneOf(s) === lane && overlaps(s.start_time, s.end_time, start, end))
}

/**
 * The changes a drop makes: the moving clips shift by `deltaSecs` (the group
 * never before 0) and keep their rows relative to the grabbed clip. A drop that
 * would overlap a clip on its target track becomes a new track just below the
 * grabbed clip's target row instead. A new track takes a free lane number
 * between its neighbours when there is one, else every clip on the tracks
 * below moves down (emitted as lane changes).
 */
export function planDrop(segs: Placed[], moving: string[], grabbedId: string, deltaSecs: number, spot: DropSpot): LayoutChange[] {
  const ids = new Set(moving)
  const movers = segs.filter((s) => ids.has(s.id))
  const grabbed = movers.find((s) => s.id === grabbedId)
  if (!grabbed || !movers.length) return []
  const rows = trackRows(segs)
  const rowOf = (s: Placed) => rows.indexOf(laneOf(s))
  const delta = Math.max(deltaSecs, -Math.min(...movers.map((s) => s.start_time)))
  const moved = (s: Placed) => ({ start: s.start_time + delta, end: s.end_time + delta })

  const place = (lanes: Map<string, number>, shifts: Map<string, number>): LayoutChange[] => {
    const changes: LayoutChange[] = []
    for (const s of movers) {
      const { start, end } = moved(s)
      const lane = lanes.get(s.id)!
      if (Math.abs(start - s.start_time) < 1e-9 && lane === laneOf(s)) continue
      changes.push({ id: s.id, start_time: round6(start), end_time: round6(end), lane_index: lane })
    }
    for (const [id, lane] of shifts) changes.push({ id, lane_index: lane })
    return changes
  }

  const insertAt = (boundary: number): LayoutChange[] => {
    const moverRows = movers.map(rowOf)
    const top = Math.min(...moverRows)
    const k = Math.max(...moverRows) - top + 1
    const lo = boundary === 0 ? -1 : rows[boundary - 1]
    const hi = boundary >= rows.length ? Infinity : rows[boundary]
    const base = lo + 1
    const shifts = new Map<string, number>()
    if (hi - lo - 1 < k) {
      const by = base + k - hi
      for (const s of segs) if (!ids.has(s.id) && laneOf(s) >= hi) shifts.set(s.id, laneOf(s) + by)
    }
    const lanes = new Map(movers.map((s) => [s.id, base + rowOf(s) - top]))
    return place(lanes, shifts)
  }

  if (spot.kind === 'insert') return insertAt(spot.boundary)

  const rowDelta = spot.row - rowOf(grabbed)
  const lanes = new Map(movers.map((s) => [s.id, rows[Math.max(0, Math.min(rows.length - 1, rowOf(s) + rowDelta))]]))
  const blocked = movers.some((s) => {
    const { start, end } = moved(s)
    const lane = lanes.get(s.id)!
    if (!fitsOnLane(segs, lane, start, end, ids)) return true
    // two moving clips clamped onto the same track
    return movers.some((o) => o.id !== s.id && lanes.get(o.id) === lane && overlaps(start, end, moved(o).start, moved(o).end))
  })
  if (blocked) return insertAt(Math.max(0, Math.min(rows.length - 1, spot.row)) + 1)
  return place(lanes, new Map())
}

/** The clip Q / W act on: the selected one under the playhead, else the top one there. */
export function clipAtPlayhead(segs: Placed[], t: number, activeId: string | null): Placed | null {
  const under = segs.filter((s) => s.start_time - EPS <= t && t <= s.end_time + EPS)
  const active = under.find((s) => s.id === activeId)
  if (active) return active
  return under.sort((a, b) => laneOf(a) - laneOf(b) || a.start_time - b.start_time)[0] ?? null
}

const round6 = (x: number) => Math.round(x * 1e6) / 1e6
