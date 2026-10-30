// src/components/speakers/CharacterFace.tsx
// A character's round face: the actor's face cut from the episode when one was
// made (face_crop_url), else the speaker's colour with its initial.
import { useState } from 'react'
import type { Speaker } from '@/types'
import { characterName } from '@/lib/whoCheck'
import { cn } from '@/lib/utils'

interface CharacterFaceProps {
  speaker: Pick<Speaker, 'display_name' | 'label' | 'face_crop_url'>
  color: string
  size?: number
  className?: string
}

export function CharacterFace({ speaker, color, size = 20, className }: CharacterFaceProps) {
  // A thumbnail that can't load (removed meanwhile) falls back to the initial
  const [broken, setBroken] = useState<string | null>(null)
  const url = speaker.face_crop_url && speaker.face_crop_url !== broken ? speaker.face_crop_url : null
  const box = { width: size, height: size }
  if (url) {
    return (
      <img
        src={url}
        alt=""
        draggable={false}
        onError={() => setBroken(url)}
        className={cn('shrink-0 rounded-full object-cover bg-ink-800', className)}
        style={box}
      />
    )
  }
  return (
    <span
      aria-hidden
      className={cn('shrink-0 inline-flex items-center justify-center rounded-full font-bold text-ink-950 select-none', className)}
      style={{ ...box, backgroundColor: color, fontSize: Math.max(8, Math.round(size * 0.45)) }}
    >
      {characterName(speaker).trim().charAt(0).toUpperCase() || '?'}
    </span>
  )
}
