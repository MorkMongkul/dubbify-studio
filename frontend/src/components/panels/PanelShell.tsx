// src/components/panels/PanelShell.tsx
// Shared chrome for the dynamic dock panels the icon rail switches between —
// one consistent header strip and one consistent empty state, so every tab of
// the dock reads as the same surface.
import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

interface PanelHeaderProps {
  icon: LucideIcon
  title: string
  count?: number
  action?: ReactNode
}

export function PanelHeader({ icon: Icon, title, count, action }: PanelHeaderProps) {
  return (
    <div className="h-9 border-b border-zinc-800/50 flex items-center gap-2 px-3 shrink-0 bg-zinc-900">
      <Icon size={13} className="text-purple-400" />
      <span className="text-xs font-semibold text-zinc-200">{title}</span>
      {count !== undefined && (
        <span className="text-[10px] text-zinc-600 tabular-nums">{count}</span>
      )}
      {action && <div className="ml-auto flex items-center gap-1">{action}</div>}
    </div>
  )
}

interface PanelEmptyStateProps {
  icon: LucideIcon
  title: string
  hint?: string
  actionLabel?: string
  onAction?: () => void
}

export function PanelEmptyState({ icon: Icon, title, hint, actionLabel, onAction }: PanelEmptyStateProps) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
      <Icon size={18} className="text-zinc-700 mb-2" />
      <p className="text-[10px] text-zinc-600">{title}</p>
      {hint && <p className="text-[9px] text-zinc-700 mt-0.5 max-w-[200px] leading-normal">{hint}</p>}
      {actionLabel && onAction && (
        <button
          onClick={onAction}
          className="mt-2.5 text-[10px] font-semibold text-purple-400 hover:text-purple-300 px-2 py-1 rounded hover:bg-purple-500/10 transition-colors"
        >
          {actionLabel}
        </button>
      )}
    </div>
  )
}
