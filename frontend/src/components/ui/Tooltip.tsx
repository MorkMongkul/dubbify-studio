// src/components/ui/Tooltip.tsx
import { type ReactNode, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '@/lib/utils'

interface TooltipProps {
  children: ReactNode
  content: string
  side?: 'top' | 'bottom' | 'left' | 'right'
  className?: string
}

const sideStyles = {
  top:    'bottom-full left-1/2 -translate-x-1/2 mb-2',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
  left:   'right-full top-1/2 -translate-y-1/2 mr-2',
  right:  'left-full top-1/2 -translate-y-1/2 ml-2',
}

const sideMotion = {
  top:    { initial: { opacity: 0, y: 6 },  animate: { opacity: 1, y: 0 } },
  bottom: { initial: { opacity: 0, y: -6 }, animate: { opacity: 1, y: 0 } },
  left:   { initial: { opacity: 0, x: 6 },  animate: { opacity: 1, x: 0 } },
  right:  { initial: { opacity: 0, x: -6 }, animate: { opacity: 1, x: 0 } },
}

export function Tooltip({ children, content, side = 'top', className }: TooltipProps) {
  const [visible, setVisible] = useState(false)
  const motion_ = sideMotion[side]

  return (
    <div
      className="relative inline-flex"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      {children}
      <AnimatePresence>
        {visible && content && (
          <motion.div
            className={cn(
              'absolute z-50 pointer-events-none whitespace-nowrap',
              sideStyles[side]
            )}
            initial={motion_.initial}
            animate={motion_.animate}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
          >
            <span className={cn(
              'px-2 py-1 rounded-md text-xs font-medium',
              'bg-surface-5 text-text-primary border border-border',
              'shadow-lg',
              className
            )}>
              {content}
            </span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
