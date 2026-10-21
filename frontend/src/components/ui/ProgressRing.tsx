// src/components/ui/ProgressRing.tsx
// A wait's progress: a ring filling with the Studio gradient, the percentage
// in the middle and a short title below with a light sweeping across it
// (.shimmer-text) while it works. Finished: a check and "Done", still.
import { useId } from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

const SIZE = 124
const RADIUS = 52
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

interface ProgressRingProps {
  /** 0–99 while working (it never says 100% before it's done) */
  percent: number
  title: string
  done?: boolean
  className?: string
}

export function ProgressRing({ percent, title, done = false, className }: ProgressRingProps) {
  const gradient = `ring-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  const shown = done ? 100 : Math.max(0, Math.min(99, Math.floor(percent)))
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={shown}
      aria-label={done ? 'Done' : title}
      className={cn('flex flex-col items-center gap-3 select-none', className)}
    >
      <div className="relative" style={{ width: SIZE, height: SIZE }}>
        <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} className="-rotate-90">
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#FF6A5C" />
              <stop offset="100%" stopColor="#E2366E" />
            </linearGradient>
          </defs>
          <circle cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" strokeWidth={9} style={{ stroke: 'var(--color-border)' }} />
          <circle
            cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} fill="none" strokeWidth={9} strokeLinecap="round"
            stroke={`url(#${gradient})`}
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={CIRCUMFERENCE * (1 - shown / 100)}
            style={{ transition: 'stroke-dashoffset 0.6s ease' }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          {done ? (
            <Check size={38} strokeWidth={2.5} className="text-emerald-400" />
          ) : (
            <span className="text-[30px] font-bold leading-none text-white tabular-nums">
              {shown}
              <span className="ml-0.5 text-[14px] font-semibold text-white/70">%</span>
            </span>
          )}
        </div>
      </div>
      <p className={cn('text-[13px] font-semibold text-center leading-snug', done ? 'text-emerald-400' : 'shimmer-text')}>
        {done ? 'Done' : title}
      </p>
    </div>
  )
}
