// src/components/transcript/CharacterMenu.tsx
// "Someone else…": every character of the project, each with its face — this
// episode's first, then the others — and "＋ New character" (the New Speaker
// window, then the pick).
import type { Speaker } from '@/types'
import { ContextMenu, type MenuItem } from '@/components/ui/ContextMenu'
import { CharacterFace } from '@/components/speakers/CharacterFace'
import { characterName } from '@/lib/whoCheck'

const FACE = 20

interface CharacterMenuProps {
  x: number
  y: number
  choices: { speaker: Speaker; inEpisode: boolean }[]
  colorOf: (speaker: Speaker) => string
  onPick: (speaker: Speaker) => void
  onNew: () => void
  onClose: () => void
}

export function CharacterMenu({ x, y, choices, colorOf, onPick, onNew, onClose }: CharacterMenuProps) {
  const list: MenuItem[] = []
  choices.forEach(({ speaker, inEpisode }, i) => {
    // A line between this episode's characters and the others
    if (i > 0 && !inEpisode && choices[i - 1].inEpisode) list.push('separator')
    list.push({
      id: speaker.id,
      label: characterName(speaker),
      leading: <CharacterFace speaker={speaker} color={colorOf(speaker)} size={FACE} />,
      onSelect: () => onPick(speaker),
    })
  })
  if (list.length) list.push('separator')
  list.push({
    id: '__new__',
    label: '＋ New character',
    leading: <span aria-hidden className="shrink-0" style={{ width: FACE }} />,
    onSelect: onNew,
  })

  return <ContextMenu x={x} y={y} items={list} onClose={onClose} />
}
