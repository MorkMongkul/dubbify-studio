// src/pages/SettingsPage.tsx
import { motion } from 'framer-motion'
import { Sun, Moon, Monitor, Palette, Info, Zap } from 'lucide-react'
import { useThemeStore } from '@/store/themeStore'
import { PageTransition } from '@/components/layout/PageTransition'
import { cn } from '@/lib/utils'

type ThemeOption = 'dark' | 'light'

const THEME_OPTIONS: { value: ThemeOption; icon: typeof Sun; label: string; description: string }[] = [
  {
    value: 'dark',
    icon: Moon,
    label: 'Dark',
    description: 'Cinematic dark interface — easy on the eyes',
  },
  {
    value: 'light',
    icon: Sun,
    label: 'Light',
    description: 'Clean bright interface for well-lit environments',
  },
]

function ThemeCard({
  option,
  active,
  onClick,
}: {
  option: typeof THEME_OPTIONS[0]
  active: boolean
  onClick: () => void
}) {
  const Icon = option.icon
  const isLight = option.value === 'light'

  return (
    <motion.button
      className={cn(
        'relative flex flex-col items-start gap-3 p-4 rounded-2xl border-2 text-left transition-colors duration-150 w-full',
        active
          ? 'border-brand/60 bg-brand/8'
          : 'border-border bg-surface-3 hover:border-border-strong hover:bg-surface-4'
      )}
      onClick={onClick}
      whileHover={{ scale: 1.01 }}
      whileTap={{ scale: 0.99 }}
      transition={{ duration: 0.1 }}
    >
      {/* Theme preview */}
      <div
        className={cn(
          'w-full h-20 rounded-xl border overflow-hidden relative flex flex-col',
          isLight
            ? 'bg-zinc-100 border-zinc-200'
            : 'bg-zinc-950 border-zinc-800'
        )}
      >
        {/* Mock sidebar strip */}
        <div
          className={cn(
            'absolute left-0 top-0 bottom-0 w-8',
            isLight ? 'bg-zinc-200' : 'bg-zinc-900'
          )}
        >
          <div className={cn('mx-1.5 mt-2 h-1.5 rounded-full', isLight ? 'bg-zinc-400' : 'bg-zinc-700')} />
          <div className={cn('mx-1.5 mt-1.5 h-1.5 rounded-full w-3/4', isLight ? 'bg-violet-400/60' : 'bg-violet-600/60')} />
          <div className={cn('mx-1.5 mt-1.5 h-1.5 rounded-full', isLight ? 'bg-zinc-400' : 'bg-zinc-700')} />
        </div>
        {/* Mock content area */}
        <div className="absolute left-10 right-2 top-2 bottom-2 flex flex-col gap-1.5">
          <div className={cn('h-1.5 rounded-full w-1/2', isLight ? 'bg-zinc-300' : 'bg-zinc-700')} />
          <div className="flex gap-1.5 mt-0.5">
            {[...Array(3)].map((_, i) => (
              <div
                key={i}
                className={cn('flex-1 h-10 rounded-lg', isLight ? 'bg-zinc-200' : 'bg-zinc-800')}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Label */}
      <div className="flex items-center gap-2">
        <Icon size={14} className={active ? 'text-brand-400' : 'text-white/40'} />
        <div>
          <p className={cn('text-sm font-semibold', active ? 'text-white' : 'text-white/80')}>{option.label}</p>
          <p className="text-xs text-white/40">{option.description}</p>
        </div>
      </div>

      {/* Active check */}
      {active && (
        <motion.div
          className="absolute top-3 right-3 h-5 w-5 rounded-full bg-brand flex items-center justify-center"
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        >
          <svg viewBox="0 0 12 12" className="w-3 h-3 text-white" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="2,6 5,9 10,3" />
          </svg>
        </motion.div>
      )}
    </motion.button>
  )
}

export default function SettingsPage() {
  const { theme, setTheme } = useThemeStore()

  return (
    <PageTransition>
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {/* Header */}
        <div className="px-8 pt-8 pb-6 shrink-0">
          <div className="flex items-center gap-2.5 mb-1">
            <Palette size={20} className="text-white/40" />
            <h1 className="text-2xl font-bold text-white tracking-tight">Settings</h1>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-8 pb-8 space-y-8">
          {/* Appearance section */}
          <section>
            <div className="flex items-center gap-2 mb-4">
              <Monitor size={14} className="text-white/40" />
              <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Appearance</h2>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-xl">
              {THEME_OPTIONS.map((option) => (
                <ThemeCard
                  key={option.value}
                  option={option}
                  active={theme === option.value}
                  onClick={() => setTheme(option.value)}
                />
              ))}
            </div>
          </section>

          {/* Divider */}
          <div className="h-px bg-border max-w-xl" />

          {/* About section */}
          <section>
            <div className="flex items-center gap-2 mb-4">
              <Info size={14} className="text-white/40" />
              <h2 className="text-sm font-semibold text-white uppercase tracking-wider">About</h2>
            </div>

            <div className="bg-surface-3 border border-border rounded-2xl p-5 max-w-xl">
              <div className="flex items-center gap-3 mb-4">
                <Zap size={16} className="text-white/50" />
                <div>
                  <p className="text-sm font-semibold text-white">Dubify Studio</p>
                  <p className="text-xs text-white/40">AI Movie Dubbing Platform</p>
                </div>
              </div>

              <div className="space-y-2 text-xs text-white/40">
                <div className="flex justify-between">
                  <span>Version</span>
                  <span className="text-white/60 font-mono">0.1.0</span>
                </div>
                <div className="flex justify-between">
                  <span>Frontend</span>
                  <span className="text-white/60 font-mono">React 19 · Vite · TypeScript</span>
                </div>
                <div className="flex justify-between">
                  <span>UI</span>
                  <span className="text-white/60 font-mono">TailwindCSS · Framer Motion</span>
                </div>
                <div className="flex justify-between">
                  <span>State</span>
                  <span className="text-white/60 font-mono">Zustand · TanStack Query</span>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </PageTransition>
  )
}
