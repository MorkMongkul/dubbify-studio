// src/components/voices/VoiceTags.tsx
// A voice's tags as small chips: who and how old (grey), personality (pink),
// kind of role (blue). Nothing when the voice has no tags.
import type { Voice } from '@/types'
import { ageLabel } from '@/lib/voiceTags'
import { cn } from '@/lib/utils'

const chip = 'text-[10px] leading-none px-1.5 py-[3px] rounded-full whitespace-nowrap'

export function VoiceTags({ voice, className }: { voice: Voice; className?: string }) {
  const age = ageLabel(voice)
  const personality = voice.personality ?? []
  const roles = voice.roles ?? []
  if (!age && personality.length === 0 && roles.length === 0) return null
  return (
    <div className={cn('flex flex-wrap gap-1', className)}>
      {age && <span className={cn(chip, 'bg-white/10 text-white/70')}>{age}</span>}
      {personality.map((tag) => (
        <span key={`p-${tag}`} className={cn(chip, 'bg-brand-500/15 text-brand-300')}>{tag}</span>
      ))}
      {roles.map((tag) => (
        <span key={`r-${tag}`} className={cn(chip, 'bg-blue-500/10 text-blue-400')}>{tag}</span>
      ))}
    </div>
  )
}
