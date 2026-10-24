// src/components/layout/LayoutMenu.tsx
// The layout menu in the top bar, like CapCut's: Default, Vertical, Transcript
// and Panel (lib/layouts), each with its diagram drawn from the same plan as the
// screen, the current one ticked, then Reset layout (lib/layoutStorage keeps the choice).
import { useCallback, useState, type ComponentType } from 'react'
import { ChevronDown, RotateCcw } from 'lucide-react'
import { ContextMenu, type MenuItem } from '@/components/ui/ContextMenu'
import { Tooltip } from '@/components/ui/Tooltip'
import { LAYOUTS, layoutPlan, type LayoutName } from '@/lib/layouts'

type GlyphProps = { size?: number; className?: string }

/** A layout's diagram: the player bright; the panel, transcript and timeline dim. */
export function LayoutGlyph({ layout, size = 14, className }: GlyphProps & { layout: LayoutName }) {
  const plan = layoutPlan(layout)
  const W = 24, H = 16, GAP = 1.5, TALL = 7, FIXED = 5, TOP = 9.5
  const rects: { x: number; y: number; w: number; h: number; player: boolean }[] = []
  let x0 = 0
  let x1 = W
  if (plan.tall) {
    const left = plan.tall.side === 'left'
    rects.push({ x: left ? 0 : W - TALL, y: 0, w: TALL, h: H, player: plan.tall.area === 'player' })
    if (left) x0 = TALL + GAP
    else x1 = W - TALL - GAP
  }
  const flexW = x1 - x0 - (plan.top.length - 1) * (FIXED + GAP)
  let x = x0
  for (const area of plan.top) {
    const w = area === plan.flex ? flexW : FIXED
    rects.push({ x, y: 0, w, h: TOP, player: area === 'player' })
    x += w + GAP
  }
  rects.push({ x: x0, y: TOP + GAP, w: x1 - x0, h: H - TOP - GAP, player: false })
  return (
    <svg width={size * 1.5} height={size} viewBox={`0 0 ${W} ${H}`} className={className} aria-hidden>
      {rects.map((r, i) => (
        <rect key={i} x={r.x} y={r.y} width={r.w} height={r.h} rx={1.2} fill="currentColor" opacity={r.player ? 0.95 : 0.4} />
      ))}
    </svg>
  )
}

// One icon component per layout, made once (menu items take a component)
const GLYPHS = {} as Record<LayoutName, ComponentType<GlyphProps>>
for (const { name } of LAYOUTS) {
  const Glyph = (props: GlyphProps) => <LayoutGlyph layout={name} {...props} />
  Glyph.displayName = `LayoutGlyph(${name})`
  GLYPHS[name] = Glyph
}

interface LayoutMenuProps {
  layout: LayoutName
  onChange: (name: LayoutName) => void
  /** Back to the layout's own sizes. */
  onReset: () => void
}

export function LayoutMenu({ layout, onChange, onReset }: LayoutMenuProps) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null)
  const close = useCallback(() => setAt(null), [])
  const items: MenuItem[] = [
    ...LAYOUTS.map(({ name, label }) => ({
      icon: GLYPHS[name], label, checked: name === layout, onSelect: () => onChange(name),
    })),
    'separator',
    { icon: RotateCcw, label: 'Reset layout', onSelect: onReset },
  ]
  return (
    <>
      <Tooltip content="Layout" side="bottom">
        <button
          // Part of the menu: a click on it toggles the menu instead of counting as outside
          data-context-menu
          aria-label="Layout"
          aria-haspopup="menu"
          aria-expanded={!!at}
          onClick={(e) => {
            if (at) { close(); return }
            const r = e.currentTarget.getBoundingClientRect()
            setAt({ x: r.right - 200, y: r.bottom + 6 })
          }}
          className="h-7 px-1.5 flex items-center gap-1 rounded-md text-white/60 hover:text-white hover:bg-white/8 transition-colors"
        >
          <LayoutGlyph layout={layout} size={13} />
          <ChevronDown size={12} />
        </button>
      </Tooltip>
      {at && <ContextMenu x={at.x} y={at.y} items={items} onClose={close} />}
    </>
  )
}
