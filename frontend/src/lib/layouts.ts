// src/lib/layouts.ts
// The editor's layouts, like CapCut's Default, Vertical, Attributes and Media:
// where the panel, the player and the transcript go around the timeline. The
// layout menu draws each one's icon from the same plan (components/layout/LayoutMenu).
// Pure: `npm test` runs it.

export type Area = 'panel' | 'player' | 'transcript'
export type LayoutName = 'default' | 'vertical' | 'transcript' | 'panel'

export const LAYOUTS: { name: LayoutName; label: string }[] = [
  { name: 'default', label: 'Default' },
  { name: 'vertical', label: 'Vertical' },
  { name: 'transcript', label: 'Transcript' },
  { name: 'panel', label: 'Panel' },
]

export interface LayoutPlan {
  /** An area as tall as the window, beside everything else; null: none. */
  tall: { area: Area; side: 'left' | 'right' } | null
  /** The areas above the timeline, left to right. */
  top: Area[]
  /** The area in `top` that takes the room left over (the others keep their widths). */
  flex: Area
}

const PLANS: Record<LayoutName, Omit<LayoutPlan, 'flex'>> = {
  default: { tall: null, top: ['panel', 'player', 'transcript'] },
  // For vertical (9:16) episodes: the player as tall as the window, the transcript in the middle
  vertical: { tall: { area: 'player', side: 'right' }, top: ['panel', 'transcript'] },
  transcript: { tall: { area: 'transcript', side: 'right' }, top: ['panel', 'player'] },
  panel: { tall: { area: 'panel', side: 'left' }, top: ['player', 'transcript'] },
}

/** Where each area goes; a hidden transcript leaves no gap. */
export function layoutPlan(name: LayoutName, transcriptHidden = false): LayoutPlan {
  const plan = PLANS[name]
  const tall = transcriptHidden && plan.tall?.area === 'transcript' ? null : plan.tall
  const top = plan.top.filter((a) => !(transcriptHidden && a === 'transcript'))
  const flex = top.includes('player') ? 'player' : top.includes('transcript') ? 'transcript' : top[top.length - 1]
  return { tall, top, flex }
}

/** A saved layout's name, or the default for anything else. */
export function readLayout(stored: string | null): LayoutName {
  return LAYOUTS.some((l) => l.name === stored) ? (stored as LayoutName) : 'default'
}
