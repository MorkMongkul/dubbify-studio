// src/components/panels/SettingsPanel.tsx
// Settings tab of the dock — theme + about, compacted from the old standalone
// /settings page so everything lives in the one editor window.
import { Sun, Moon, Settings2, Zap } from 'lucide-react'
import { useThemeStore } from '@/store/themeStore'
import { cn } from '@/lib/utils'
import { PanelHeader } from './PanelShell'

type ThemeOption = 'dark' | 'light'

const THEME_OPTIONS: { value: ThemeOption; icon: typeof Sun; label: string; description: string }[] = [
  { value: 'dark',  icon: Moon, label: 'Dark',  description: 'Cinematic dark interface — easy on the eyes' },
  { value: 'light', icon: Sun,  label: 'Light', description: 'Clean bright interface for well-lit rooms' },
]

function ThemeCard({
  option, active, onClick,
}: { option: typeof THEME_OPTIONS[0]; active: boolean; onClick: () => void }) {
  const Icon = option.icon
  const isLight = option.value === 'light'

  return (
    <button
      className={cn(
        'relative flex items-center gap-3 p-2.5 rounded-xl border-2 text-left transition-colors duration-150 w-full',
        active
          ? 'border-brand/60 bg-brand/8'
          : 'border-border bg-surface-3 hover:border-border-strong hover:bg-surface-4'
      )}
      onClick={onClick}
      aria-pressed={active}
    >
      {/* Mini theme preview */}
      <div className={cn(
        'h-12 w-16 shrink-0 rounded-lg border overflow-hidden relative',
        isLight ? 'bg-zinc-100 border-zinc-200' : 'bg-zinc-950 border-zinc-800'
      )}>
        <div className={cn('absolute left-0 top-0 bottom-0 w-4', isLight ? 'bg-zinc-200' : 'bg-zinc-900')}>
          <div className={cn('mx-1 mt-1.5 h-1 rounded-full', isLight ? 'bg-zinc-400' : 'bg-zinc-700')} />
          <div className={cn('mx-1 mt-1 h-1 rounded-full w-2/3', isLight ? 'bg-violet-400/60' : 'bg-violet-600/60')} />
        </div>
        <div className="absolute left-5 right-1 top-1.5 bottom-1.5 flex flex-col gap-1">
          <div className={cn('h-1 rounded-full w-1/2', isLight ? 'bg-zinc-300' : 'bg-zinc-700')} />
          <div className="flex gap-1 mt-0.5 flex-1">
            {[...Array(2)].map((_, i) => (
              <div key={i} className={cn('flex-1 rounded', isLight ? 'bg-zinc-200' : 'bg-zinc-800')} />
            ))}
          </div>
        </div>
      </div>

      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <Icon size={12} className={active ? 'text-brand-400' : 'text-white/40'} />
          <p className={cn('text-[12px] font-semibold', active ? 'text-white' : 'text-white/80')}>{option.label}</p>
        </div>
        <p className="text-[10px] text-white/40 leading-snug mt-0.5">{option.description}</p>
      </div>

      {active && (
        <div className="absolute top-2 right-2 h-4 w-4 rounded-full bg-brand flex items-center justify-center">
          <svg viewBox="0 0 12 12" className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="2,6 5,9 10,3" />
          </svg>
        </div>
      )}
    </button>
  )
}

export function SettingsPanel() {
  const { theme, setTheme } = useThemeStore()

  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader icon={Settings2} title="Settings" />
      <div className="flex-1 overflow-y-auto p-3 space-y-5">
        {/* Appearance */}
        <section className="space-y-2">
          <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Appearance</span>
          <div className="space-y-2">
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

        <div className="h-px bg-zinc-800/60" />

        {/* About */}
        <section className="space-y-2">
          <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">About</span>
          <div className="rounded-xl border border-zinc-800/60 bg-zinc-800/20 p-3">
            <div className="flex items-center gap-2 mb-2.5">
              <div className="h-6 w-6 rounded bg-brand flex items-center justify-center">
                <Zap size={12} className="text-white" fill="white" />
              </div>
              <div>
                <p className="text-[11.5px] font-semibold text-white leading-tight">Dubify Studio</p>
                <p className="text-[9.5px] text-white/40">AI Movie Dubbing Platform</p>
              </div>
            </div>
            <div className="space-y-1 text-[10px] text-white/40">
              <div className="flex justify-between"><span>Version</span><span className="text-white/60 font-mono">0.1.0</span></div>
              <div className="flex justify-between"><span>Frontend</span><span className="text-white/60 font-mono">React 19 · Vite</span></div>
              <div className="flex justify-between"><span>State</span><span className="text-white/60 font-mono">Zustand · TanStack Query</span></div>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
