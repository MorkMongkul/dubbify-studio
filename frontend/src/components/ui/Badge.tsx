// src/components/ui/Badge.tsx
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { JobStatus, StatusColor } from '@/types'

type BadgeVariant = StatusColor | 'default'

interface BadgeProps {
  children: ReactNode
  variant?: BadgeVariant
  dot?: boolean
  pulse?: boolean
  className?: string
}

const variantStyles: Record<BadgeVariant, string> = {
  default: 'bg-white/8 text-text-secondary border-border',
  violet:  'bg-brand-subtle text-brand-300 border-brand/30',
  blue:    'bg-blue-500/10 text-blue-400 border-blue-500/30',
  green:   'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  yellow:  'bg-amber-500/10 text-amber-400 border-amber-500/30',
  red:     'bg-red-500/10 text-red-400 border-red-500/30',
  gray:    'bg-zinc-500/10 text-zinc-400 border-zinc-500/30',
}

const dotColors: Record<BadgeVariant, string> = {
  default: 'bg-text-muted',
  violet:  'bg-brand-400',
  blue:    'bg-blue-400',
  green:   'bg-emerald-400',
  yellow:  'bg-amber-400',
  red:     'bg-red-400',
  gray:    'bg-zinc-400',
}

export function Badge({ children, variant = 'default', dot, pulse, className }: BadgeProps) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border',
      variantStyles[variant],
      className
    )}>
      {dot && (
        <span className="relative inline-flex h-1.5 w-1.5">
          <span className={cn('rounded-full', dotColors[variant], 'h-full w-full', pulse && 'absolute animate-ping opacity-75')} />
          {pulse && <span className={cn('relative rounded-full h-1.5 w-1.5', dotColors[variant])} />}
        </span>
      )}
      {children}
    </span>
  )
}

const statusVariantMap: Record<JobStatus, BadgeVariant> = {
  pending:      'gray',
  extracting:   'violet',
  separating:   'violet',
  diarizing:    'violet',
  transcribing: 'violet',
  translating:  'blue',
  synthesizing: 'violet',
  mixing:       'blue',
  completed:    'green',
  failed:       'red',
}

interface StatusBadgeProps {
  status: JobStatus
  label: string
  className?: string
}

export function StatusBadge({ status, label, className }: StatusBadgeProps) {
  const isRunning = ['uploading','extracting','separating','diarizing','transcribing','translating','synthesizing','mixing'].includes(status)
  return (
    <Badge
      variant={statusVariantMap[status]}
      dot
      pulse={isRunning}
      className={className}
    >
      {label}
    </Badge>
  )
}
