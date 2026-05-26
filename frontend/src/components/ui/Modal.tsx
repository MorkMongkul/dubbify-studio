// src/components/ui/Modal.tsx
import { type ReactNode, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from './Button'

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  description?: string
  children: ReactNode
  className?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
}

const sizeMap = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
  xl: 'max-w-xl',
}

export function Modal({ open, onClose, title, description, children, className, size = 'md' }: ModalProps) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open) onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ fontFamily: 'var(--font-sans)' }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.12 }}
        >
          {/* Backdrop */}
          <motion.div
            className="absolute inset-0 backdrop-blur-sm"
            style={{ background: 'var(--color-backdrop)' }}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />

          {/* Dialog */}
          <motion.div
            className={cn('relative w-full glass-modal rounded-2xl p-6 z-10', sizeMap[size], className)}
            style={{
              background: 'var(--glass-modal-bg)',
              borderColor: 'var(--color-border)',
              color: 'var(--color-text-primary)',
              fontFamily: 'var(--font-sans)',
            }}
            initial={{ scale: 0.94, opacity: 0, y: 12 }}
            animate={{ scale: 1, opacity: 1, y: 0 }}
            exit={{ scale: 0.94, opacity: 0, y: 12 }}
            transition={{ type: 'spring', stiffness: 420, damping: 32 }}
          >
            {/* Header */}
            {(title || description) && (
              <div className="mb-5">
                <div className="flex items-start justify-between gap-3">
                  {title && (
                    <h2
                      className="text-lg font-semibold"
                      style={{ color: 'var(--color-text-primary)' }}
                    >
                      {title}
                    </h2>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    className="shrink-0 h-7 w-7 rounded-md"
                    onClick={onClose}
                    icon={<X size={15} />}
                  />
                </div>
                {description && (
                  <p
                    className="text-sm mt-1"
                    style={{ color: 'var(--color-text-muted)' }}
                  >
                    {description}
                  </p>
                )}
              </div>
            )}

            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

// ── Field helpers ─────────────────────────────────────────────────

interface FieldProps {
  label: string
  required?: boolean
  error?: string
  children: ReactNode
  className?: string
}

export function Field({ label, required, error, children, className }: FieldProps) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <label
        className="block text-sm font-medium"
        style={{ color: 'var(--color-text-secondary)' }}
      >
        {label}
        {required && <span className="text-brand-400 ml-0.5">*</span>}
      </label>
      {children}
      {error && (
        <p className="text-xs text-red-400">{error}</p>
      )}
    </div>
  )
}

interface InputFieldProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string
  required?: boolean
  error?: string
}

export function InputField({ label, required, error, className, ...props }: InputFieldProps) {
  return (
    <Field label={label} required={required} error={error}>
      <input
        className={cn(
          'glass-input w-full rounded-lg px-3 py-2 text-sm',
          'focus:outline-none transition-all duration-150',
          error && 'border-red-500/50',
          className
        )}
        style={{
          background: 'var(--glass-input-bg)',
          borderColor: error ? 'rgba(239,68,68,0.5)' : 'var(--color-border)',
          color: 'var(--color-text-primary)',
          fontFamily: 'var(--font-sans)',
        }}
        {...props}
      />
    </Field>
  )
}

interface SelectFieldProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label: string
  required?: boolean
  error?: string
  options: { value: string; label: string }[]
}

export function SelectField({ label, required, error, options, className, ...props }: SelectFieldProps) {
  return (
    <Field label={label} required={required} error={error}>
      <select
        className={cn(
          'glass-input w-full rounded-lg px-3 py-2 text-sm',
          'focus:outline-none transition-all duration-150 cursor-pointer',
          error && 'border-red-500/50',
          className
        )}
        style={{
          background: 'var(--color-surface-3)',
          borderColor: error ? 'rgba(239,68,68,0.5)' : 'var(--color-border)',
          color: 'var(--color-text-primary)',
          fontFamily: 'var(--font-sans)',
        }}
        {...props}
      >
        {options.map((o) => (
          <option
            key={o.value}
            value={o.value}
            style={{
              background: 'var(--color-surface-3)',
              color: 'var(--color-text-primary)',
            }}
          >
            {o.label}
          </option>
        ))}
      </select>
    </Field>
  )
}
