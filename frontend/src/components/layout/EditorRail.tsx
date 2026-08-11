// src/components/layout/EditorRail.tsx
// CapCut-style icon rail — the far-left strip of the single-window editor.
// Each icon opens its panel in the dynamic dock next to it; nothing here
// navigates (rail tabs must never change the URL, or the auto-navigate-to-
// latest-job effect would fight the user while they browse).
import { useNavigate } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import {
  Zap, FolderOpen, Film, Users, ImagePlus, Mic, Settings2, Sun, Moon,
} from 'lucide-react'
import { useEditorStore, type RailTab } from '@/store/editorStore'
import { useThemeStore } from '@/store/themeStore'
import { Tooltip } from '@/components/ui/Tooltip'
import { cn } from '@/lib/utils'

const RAIL_ITEMS: { tab: RailTab; icon: LucideIcon; label: string }[] = [
  { tab: 'projects', icon: FolderOpen, label: 'Projects' },
  { tab: 'sessions', icon: Film,       label: 'Sessions' },
  { tab: 'speakers', icon: Users,      label: 'Speakers' },
  { tab: 'elements', icon: ImagePlus,  label: 'Elements' },
  { tab: 'voices',   icon: Mic,        label: 'Voices' },
]

function RailButton({
  active, label, icon: Icon, onClick,
}: { active: boolean; label: string; icon: LucideIcon; onClick: () => void }) {
  return (
    <Tooltip content={label} side="right">
      <button
        onClick={onClick}
        aria-pressed={active}
        aria-label={label}
        className={cn(
          'h-9 w-9 rounded-lg flex items-center justify-center transition-colors',
          active
            ? 'bg-purple-500/15 text-purple-300'
            : 'text-white/40 hover:text-white hover:bg-white/6'
        )}
      >
        <Icon size={16} />
      </button>
    </Tooltip>
  )
}

export function EditorRail() {
  const navigate = useNavigate()
  const railTab = useEditorStore((s) => s.railTab)
  const setRailTab = useEditorStore((s) => s.setRailTab)
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggleTheme)

  return (
    <aside
      className="w-12 shrink-0 h-full flex flex-col items-center border-r py-2 gap-1 select-none z-10"
      style={{ background: 'var(--color-surface-1)', borderColor: 'var(--color-border)' }}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Brand tile — Home (clears the open project/session) */}
      <Tooltip content="Home" side="right">
        <button
          onClick={() => navigate('/')}
          aria-label="Home"
          className="h-8 w-8 rounded-lg bg-brand flex items-center justify-center mb-2 hover:scale-105 transition-transform shadow-glow-sm"
        >
          <Zap size={14} className="text-white" fill="white" />
        </button>
      </Tooltip>

      {RAIL_ITEMS.map(({ tab, icon, label }) => (
        <RailButton
          key={tab}
          active={railTab === tab}
          label={label}
          icon={icon}
          onClick={() => setRailTab(tab)}
        />
      ))}

      <div className="flex-1" />

      <Tooltip content={theme === 'dark' ? 'Light mode' : 'Dark mode'} side="right">
        <button
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className="h-9 w-9 rounded-lg flex items-center justify-center text-white/40 hover:text-white hover:bg-white/6 transition-colors"
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </Tooltip>
      <RailButton
        active={railTab === 'settings'}
        label="Settings"
        icon={Settings2}
        onClick={() => setRailTab('settings')}
      />
    </aside>
  )
}
