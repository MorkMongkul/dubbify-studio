// src/components/ui/Tooltip.tsx
// Portal-based tooltip: rendered into document.body with fixed positioning so
// it can never be clipped by an overflow-hidden ancestor (the timeline panel,
// the dock's scroll area, …) — clipped tooltips looked like broken fragments
// bleeding into the neighbouring panel.
import { type ReactNode, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '@/lib/utils'

interface TooltipProps {
  children: ReactNode
  content: string
  side?: 'top' | 'bottom' | 'left' | 'right'
  className?: string
}

const GAP = 8

// Anchor-point translation per side — applied on a plain inner div, NOT the
// motion.div: framer-motion owns the motion element's `transform`, and a CSS
// transform there would be overwritten on the first animation frame.
const SIDE_TRANSFORM: Record<NonNullable<TooltipProps['side']>, string> = {
  top:    'translate(-50%, -100%)',
  bottom: 'translate(-50%, 0)',
  left:   'translate(-100%, -50%)',
  right:  'translate(0, -50%)',
}

export function Tooltip({ children, content, side = 'top', className }: TooltipProps) {
  const anchorRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)

  const show = () => {
    const r = anchorRef.current?.getBoundingClientRect()
    if (!r) return
    setPos(
      side === 'top'    ? { x: r.left + r.width / 2,  y: r.top - GAP }
      : side === 'bottom' ? { x: r.left + r.width / 2,  y: r.bottom + GAP }
      : side === 'left'   ? { x: r.left - GAP,           y: r.top + r.height / 2 }
      :                     { x: r.right + GAP,          y: r.top + r.height / 2 }
    )
  }

  // Once the bubble has real dimensions, nudge it back inside the viewport —
  // an anchor near a screen edge would otherwise push a centred tooltip
  // partly off-screen (e.g. the timeline's leftmost tool buttons). Ref
  // callbacks run before paint, so the nudge is never visible as a jump.
  const clampToViewport = (node: HTMLDivElement | null) => {
    if (!node) return
    const span = node.querySelector('span')
    if (!span) return
    const b = span.getBoundingClientRect()
    const vw = window.innerWidth
    const vh = window.innerHeight
    let dx = 0, dy = 0
    if (b.left < 4) dx = 4 - b.left
    else if (b.right > vw - 4) dx = vw - 4 - b.right
    if (b.top < 4) dy = 4 - b.top
    else if (b.bottom > vh - 4) dy = vh - 4 - b.bottom
    if (dx) node.style.left = `${parseFloat(node.style.left) + dx}px`
    if (dy) node.style.top = `${parseFloat(node.style.top) + dy}px`
  }

  return (
    <div
      ref={anchorRef}
      className="relative inline-flex"
      onMouseEnter={show}
      onMouseLeave={() => setPos(null)}
      // Pressing a tool swaps/uses it immediately — keep the hint from
      // lingering over the click.
      onMouseDown={() => setPos(null)}
    >
      {children}
      {createPortal(
        <AnimatePresence>
          {pos && content && (
            <motion.div
              ref={clampToViewport}
              className="fixed z-[200] pointer-events-none whitespace-nowrap"
              style={{ left: pos.x, top: pos.y }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.12 }}
            >
              <div style={{ transform: SIDE_TRANSFORM[side] }}>
                <span className={cn(
                  'block px-2 py-1 rounded-md text-xs font-medium',
                  'bg-surface-5 text-text-primary border border-border',
                  'shadow-lg',
                  className
                )}>
                  {content}
                </span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body
      )}
    </div>
  )
}
