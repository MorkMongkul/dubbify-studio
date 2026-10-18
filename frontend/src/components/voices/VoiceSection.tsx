// src/components/voices/VoiceSection.tsx
// One section of the Voices tab: a header (open/close, name, age range,
// count) over its voices. Without onToggle (while searching) it stays open.
import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Props {
  label: string
  hint?: string
  count: number
  open: boolean
  onToggle?: () => void
  children: ReactNode
}

export function VoiceSection({ label, hint, count, open, onToggle, children }: Props) {
  return (
    <section className="border-b border-white/[0.06] last:border-b-0">
      <button
        onClick={onToggle}
        disabled={!onToggle}
        aria-expanded={open}
        className="group w-full flex items-baseline gap-1.5 px-0.5 py-2 text-left disabled:cursor-default"
      >
        <ChevronRight
          size={11}
          className={cn('self-center shrink-0 text-white/35 transition-transform', open && 'rotate-90')}
        />
        <span className="text-[11.5px] font-semibold text-white/80 group-hover:text-white">{label}</span>
        {hint && <span className="text-[10px] text-white/35">{hint}</span>}
        <span className="ml-auto text-[10px] text-white/35 tabular-nums">{count}</span>
      </button>
      {open && <div className="space-y-1.5 pb-2.5">{children}</div>}
    </section>
  )
}
