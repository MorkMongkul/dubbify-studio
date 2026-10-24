// src/components/timeline/TrackSwitch.tsx
// One switch on a track's header (lock, hide, mute): amber while on, like the music row's
import type { LucideIcon } from 'lucide-react'
import { Tooltip } from '@/components/ui/Tooltip'
import { cn } from '@/lib/utils'

export function TrackSwitch({ on, label, icon: Icon, onClick }: { on: boolean; label: string; icon: LucideIcon; onClick: () => void }) {
  return (
    <Tooltip content={label} side="bottom">
      <button
        onClick={onClick}
        aria-label={label}
        aria-pressed={on}
        className={cn(
          'h-6 w-6 rounded-md flex items-center justify-center transition-colors shrink-0',
          on ? 'bg-amber-500/20 text-amber-400' : 'text-white/45 hover:text-white hover:bg-white/8',
        )}
      >
        <Icon size={13} />
      </button>
    </Tooltip>
  )
}
