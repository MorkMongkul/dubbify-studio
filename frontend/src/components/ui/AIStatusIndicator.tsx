// src/components/ui/AIStatusIndicator.tsx
import { motion } from 'framer-motion'
import { Cpu } from 'lucide-react'
import { cn } from '@/lib/utils'

interface AIStatusIndicatorProps {
  active?: boolean
  label?: string
  size?: 'sm' | 'md'
  className?: string
}

export function AIStatusIndicator({ active = false, label, size = 'sm', className }: AIStatusIndicatorProps) {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <div className="relative">
        {active && (
          <>
            <motion.div
              className={cn(
                'absolute inset-0 rounded-full bg-brand/40',
                size === 'md' ? 'scale-150' : 'scale-125'
              )}
              animate={{ scale: [1, 1.5, 1], opacity: [0.4, 0, 0.4] }}
              transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
            />
            <motion.div
              className="absolute inset-0 rounded-full bg-brand/20"
              animate={{ scale: [1, 2, 1], opacity: [0.2, 0, 0.2] }}
              transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut', delay: 0.3 }}
            />
          </>
        )}
        <motion.div
          className={cn(
            'relative flex items-center justify-center rounded-full',
            size === 'md' ? 'h-8 w-8' : 'h-5 w-5',
            active ? 'bg-brand/20 text-brand-300' : 'bg-surface-4 text-text-muted'
          )}
          animate={active ? { scale: [1, 1.05, 1] } : {}}
          transition={{ duration: 1.5, repeat: Infinity }}
        >
          <Cpu size={size === 'md' ? 16 : 11} />
        </motion.div>
      </div>

      {label && (
        <span className={cn(
          'font-medium',
          size === 'md' ? 'text-sm' : 'text-xs',
          active ? 'text-brand-300' : 'text-text-muted'
        )}>
          {label}
        </span>
      )}
    </div>
  )
}

// Pulsing dot indicator (e.g. for header status)
export function StatusDot({ active, color = 'violet' }: { active: boolean; color?: string }) {
  return (
    <span className="relative flex h-2 w-2">
      {active && (
        <span
          className="absolute inline-flex h-full w-full rounded-full opacity-75 animate-ping"
          style={{ background: color === 'violet' ? '#7C3AED' : '#10B981' }}
        />
      )}
      <span
        className="relative inline-flex rounded-full h-2 w-2"
        style={{ background: active ? (color === 'violet' ? '#7C3AED' : '#10B981') : '#52525B' }}
      />
    </span>
  )
}
