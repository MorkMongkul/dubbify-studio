// src/components/voices/VoiceTrigger.tsx
// The voice chosen for a speaker, as a box that opens the voice browser: ▶ to
// listen, the voice's name and tags, or the automatic choice when there's none.
import { ChevronRight } from 'lucide-react'
import type { Voice } from '@/types'
import { cn } from '@/lib/utils'
import { VoicePlayer } from './VoicePlayer'
import { VoiceTags } from './VoiceTags'

interface VoiceTriggerProps {
  /** The chosen voice; undefined for the automatic choice (or a voice that no longer exists). */
  voice?: Voice
  /** A voice id is set but that voice isn't in the library any more. */
  missing?: boolean
  emptyLabel: string
  emptyHint: string
  /** Unique per box, so its player stops when another starts. */
  playerId: string
  onOpen: () => void
  title?: string
  className?: string
}

export function VoiceTrigger({ voice, missing, emptyLabel, emptyHint, playerId, onOpen, title, className }: VoiceTriggerProps) {
  const url = voice?.reference_audio_url ? `${voice.reference_audio_url}?v=${voice.seed}` : null
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return
        e.stopPropagation()                        // Space would also play/pause the video
        if (e.target !== e.currentTarget) return   // the play button inside handles its own keys
        e.preventDefault()
        onOpen()
      }}
      title={title}
      className={cn(
        'flex items-center gap-2 rounded-md border border-ink-700/60 bg-ink-900 hover:border-ink-600 hover:bg-ink-800/60 p-1.5 cursor-pointer transition-colors focus:outline-none focus:border-brand-500/60',
        className,
      )}
    >
      {voice && (
        <VoicePlayer compact key={url ?? 'none'} id={playerId} name={voice.name} url={url} onGenerate={onOpen} generating={false} />
      )}
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold text-ink-100 truncate">
          {voice?.name ?? (missing ? 'Voice not found' : emptyLabel)}
        </p>
        {voice ? (
          <VoiceTags voice={voice} className="mt-1" />
        ) : (
          <p className="text-[9.5px] text-ink-500 mt-0.5 leading-snug">{missing ? 'Choose another voice' : emptyHint}</p>
        )}
      </div>
      <ChevronRight size={13} className="text-ink-500 shrink-0" />
    </div>
  )
}
