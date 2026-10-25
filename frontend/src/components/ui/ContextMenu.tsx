// src/components/ui/ContextMenu.tsx
// A right-click menu: icons, names and shortcuts (menus keep their words, as in
// CapCut). Rendered into document.body at the pointer, kept inside the window;
// closes on a click outside, Escape, scrolling or choosing an item. An item with
// `items` opens a submenu beside it.
import { useEffect, useLayoutEffect, useRef, useState, type ComponentType, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

export type MenuItem = {
  /** Tells items apart when two share a label (e.g. two characters with one name). */
  id?: string
  /** A lucide icon, or any icon drawn at `size` (the layout menu's diagrams). */
  icon?: ComponentType<{ size?: number; className?: string }>
  /** Drawn in the icon's place instead (a character's face). */
  leading?: ReactNode
  label: string
  shortcut?: string
  onSelect?: () => void
  items?: MenuItem[]
  disabled?: boolean
  /** A tick beside the item (e.g. the current speed). */
  checked?: boolean
} | 'separator'

interface ContextMenuProps {
  x: number
  y: number
  items: MenuItem[]
  onClose: () => void
}

const MARGIN = 8

function MenuList({ x, y, items, onClose, nested = false }: ContextMenuProps & { nested?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ left: x, top: y })
  const [open, setOpen] = useState<{ index: number; x: number; y: number } | null>(null)

  // Keep the menu inside the window
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const left = Math.max(MARGIN, Math.min(x, window.innerWidth - r.width - MARGIN))
    const top = Math.max(MARGIN, Math.min(y, window.innerHeight - r.height - MARGIN))
    if (left !== pos.left || top !== pos.top) setPos({ left, top })
  }, [x, y, pos.left, pos.top])

  return (
    <div
      ref={ref}
      role="menu"
      data-context-menu
      className={cn(
        // A long list (every character of a project) scrolls inside the window
        'fixed z-[1000] min-w-[200px] max-h-[calc(100vh-16px)] overflow-y-auto rounded-lg border border-white/10 bg-ink-900 py-1 shadow-2xl',
        nested && 'z-[1001]',
      )}
      style={{ left: pos.left, top: pos.top }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((item, i) => {
        if (item === 'separator') return <div key={`sep-${i}`} className="my-1 h-px bg-white/[0.08]" />
        const Icon = item.icon
        return (
          <button
            key={item.id ?? item.label}
            role="menuitem"
            disabled={item.disabled}
            className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-[12.5px] text-white/85 hover:bg-white/[0.08] disabled:opacity-35 disabled:hover:bg-transparent"
            onMouseEnter={(e) => {
              if (!item.items) { setOpen(null); return }
              const r = e.currentTarget.getBoundingClientRect()
              setOpen({ index: i, x: r.right + 2, y: r.top - 4 })
            }}
            onClick={(e) => {
              e.stopPropagation()
              if (item.items) {
                const r = e.currentTarget.getBoundingClientRect()
                setOpen({ index: i, x: r.right + 2, y: r.top - 4 })
                return
              }
              item.onSelect?.()
              onClose()
            }}
          >
            {item.leading ?? (Icon && <Icon size={14} className="shrink-0 text-white/60" />)}
            <span className="grow">{item.label}</span>
            {item.checked && <span className="text-brand-300">✓</span>}
            {item.shortcut && <span className="text-[11px] text-white/35 tabular-nums">{item.shortcut}</span>}
            {item.items && <ChevronRight size={13} className="text-white/40" />}
          </button>
        )
      })}
      {open && (() => {
        const item = items[open.index]
        // Its own portal: inside this menu, `position: fixed` could be relative to it
        return item !== 'separator' && item.items
          ? createPortal(<MenuList x={open.x} y={open.y} items={item.items} onClose={onClose} nested />, document.body)
          : null
      })()}
    </div>
  )
}

export function ContextMenu({ x, y, items, onClose }: ContextMenuProps) {
  useEffect(() => {
    const outside = (e: MouseEvent) => {
      if (!(e.target as HTMLElement)?.closest?.('[data-context-menu]')) onClose()
    }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    // Scrolling the page closes it; scrolling a long menu itself doesn't
    const scroll = (e: Event) => {
      if (e.target instanceof Element && e.target.closest('[data-context-menu]')) return
      onClose()
    }
    window.addEventListener('mousedown', outside, true)
    window.addEventListener('keydown', key, true)
    window.addEventListener('scroll', scroll, true)
    window.addEventListener('blur', scroll)
    return () => {
      window.removeEventListener('mousedown', outside, true)
      window.removeEventListener('keydown', key, true)
      window.removeEventListener('scroll', scroll, true)
      window.removeEventListener('blur', scroll)
    }
  }, [onClose])

  return createPortal(<MenuList x={x} y={y} items={items} onClose={onClose} />, document.body)
}
