// src/components/ui/ProgressSteps.tsx
// One dot per step a running stage really has (lib/jobProgress.ts). The
// backend only knows which step it's in, not how far through it, so the dots
// say done / now / not yet — never a percentage.
import { motion } from 'framer-motion'
import type { ProgressStep } from '@/lib/jobProgress'
import { cn } from '@/lib/utils'

export function ProgressSteps({ steps, className }: { steps: ProgressStep[]; className?: string }) {
  if (steps.length === 0) return null
  const current = steps.findIndex((s) => s.state === 'active')

  return (
    <div className={cn('space-y-1.5', className)}>
      <span className="block text-[11px] text-white/40">
        {current < 0 ? 'Waiting to start' : `Step ${current + 1} of ${steps.length}`}
      </span>
      <div className="flex items-center gap-1">
        {steps.map((step) => (
          <div
            key={step.label}
            title={step.label}
            className={cn(
              'h-1.5 flex-1 rounded-full transition-colors duration-300',
              step.state === 'done'    && 'bg-emerald-500/70',
              step.state === 'pending' && 'bg-white/10',
            )}
            style={step.state === 'active' ? { background: 'var(--color-brand, #D6305F)' } : undefined}
          >
            {step.state === 'active' && (
              <motion.div
                className="h-full w-full rounded-full bg-gradient-to-r from-brand to-accent"
                animate={{ opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 1.2, repeat: Infinity, ease: 'easeInOut' }}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
