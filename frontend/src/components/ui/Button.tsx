// src/components/ui/Button.tsx
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'

type Variant = 'default' | 'ghost' | 'outline' | 'danger' | 'accent' | 'glass'
type Size = 'xs' | 'sm' | 'md' | 'lg' | 'icon'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  icon?: ReactNode
  iconRight?: ReactNode
  children?: ReactNode
}

const variantStyles: Record<Variant, string> = {
  default: 'bg-brand text-white hover:bg-brand-hover shadow-glow-sm hover:shadow-glow border border-brand/30',
  ghost:   'bg-transparent text-text-secondary hover:bg-white/6 hover:text-text-primary border border-transparent',
  outline: 'bg-transparent text-text-primary border border-border hover:border-border-strong hover:bg-white/5',
  danger:  'bg-status-error/10 text-status-error hover:bg-status-error/20 border border-status-error/30',
  accent:  'bg-accent text-white hover:bg-accent-hover border border-accent/30',
  glass:   'glass text-text-primary hover:bg-white/8 hover:border-border-strong',
}

const sizeStyles: Record<Size, string> = {
  xs:   'h-6 px-2 text-xs gap-1 rounded',
  sm:   'h-7 px-3 text-sm gap-1.5 rounded-md',
  md:   'h-9 px-4 text-sm gap-2 rounded-lg',
  lg:   'h-11 px-6 text-base gap-2.5 rounded-xl',
  icon: 'h-9 w-9 rounded-lg p-0 justify-center',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'default', size = 'md', loading, icon, iconRight, children, className, disabled, onClick, ...props }, ref) => {
    const isDisabled = disabled || loading

    return (
      <motion.button
        ref={ref}
        whileHover={isDisabled ? undefined : { scale: 1.02 }}
        whileTap={isDisabled ? undefined : { scale: 0.97 }}
        transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        className={cn(
          'inline-flex items-center font-medium transition-all duration-150 cursor-pointer select-none',
          'disabled:opacity-40 disabled:cursor-not-allowed disabled:pointer-events-none',
          variantStyles[variant],
          sizeStyles[size],
          className
        )}
        disabled={isDisabled}
        onClick={onClick as React.MouseEventHandler<HTMLButtonElement>}
        {...(props as object)}
      >
        {loading ? (
          <span className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
        ) : icon}
        {children && <span>{children}</span>}
        {iconRight && !loading && iconRight}
      </motion.button>
    )
  }
)
Button.displayName = 'Button'
