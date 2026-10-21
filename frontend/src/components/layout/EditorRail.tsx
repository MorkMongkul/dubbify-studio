// src/components/layout/EditorRail.tsx
// CapCut-style icon rail — the far-left strip of the single-window editor.
// Each icon opens its panel in the dynamic dock next to it, and the gear the
// Settings window; nothing here navigates (rail tabs must never change the
// URL, or the auto-navigate-to-latest-job effect would fight the user while
// they browse).
import { useNavigate } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import {
  FolderOpen, Users, ImagePlus, Mic, Bot, Settings,
} from 'lucide-react'
import { useEditorStore, type RailTab } from '@/store/editorStore'
import { Tooltip } from '@/components/ui/Tooltip'
import { MoveloStudioIcon } from '@/components/brand/Movelo'
import { cn } from '@/lib/utils'

const RAIL_ITEMS: { tab: RailTab; icon: LucideIcon; label: string }[] = [
  { tab: 'projects',  icon: FolderOpen, label: 'Projects' },
  { tab: 'speakers',  icon: Users,      label: 'Speakers' },
  { tab: 'elements',  icon: ImagePlus,  label: 'Elements' },
  { tab: 'voices',    icon: Mic,        label: 'Voices' },
  { tab: 'assistant', icon: Bot,        label: 'Assistant' },
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
            ? 'bg-brand-500/15 text-brand-300'
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
  const settingsOpen = useEditorStore((s) => s.settingsOpen)
  const setSettingsOpen = useEditorStore((s) => s.setSettingsOpen)

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
          className="h-8 w-8 flex items-center justify-center mb-2 hover:scale-105 transition-transform"
        >
          <MoveloStudioIcon size={28} />
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

      <RailButton
        active={settingsOpen}
        label="Settings (⌘,)"
        icon={Settings}
        onClick={() => setSettingsOpen(true)}
      />
    </aside>
  )
}
