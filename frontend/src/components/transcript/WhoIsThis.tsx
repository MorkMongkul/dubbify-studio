// src/components/transcript/WhoIsThis.tsx
// Under the text of a line Movelo isn't sure about: its two best guesses —
// face, name, what the guess rests on and, on the line being checked, the key
// (1 / 2) that picks it; the best guess has an amber border — and
// "Someone else…", which lists every character of the project.
import { useCallback, useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { Speaker } from '@/types'
import type { GuessChip } from '@/lib/whoCheck'
import { characterName } from '@/lib/whoCheck'
import type { PendingPick } from '@/hooks/useCharacterPicks'
import { CharacterFace } from '@/components/speakers/CharacterFace'
import { CharacterMenu } from './CharacterMenu'
import { cn } from '@/lib/utils'

interface WhoIsThisProps {
  chips: GuessChip[]
  choices: { speaker: Speaker; inEpisode: boolean }[]
  colorOf: (speaker: Speaker) => string
  /** Keys 1 / 2 pick for this line: its chips show them. */
  checking: boolean
  /** A pick on its way for this line (it waits for the Khmer to be rewritten). */
  pending: PendingPick | null
  onPick: (speaker: Speaker, from: 'chip' | 'menu') => void
  onNew: () => void
}

const CHIP = 'inline-flex items-center gap-1.5 h-[26px] rounded-full border bg-ink-900 text-[11px] transition-colors'

export function WhoIsThis({ chips, choices, colorOf, checking, pending, onPick, onNew }: WhoIsThisProps) {
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const closeMenu = useCallback(() => setMenu(null), [])
  const busy = !!pending

  return (
    // A click here picks; it mustn't also choose the row (which seeks to it)
    <div className="flex flex-wrap items-center gap-1.5 px-1.5 pt-1 pb-0.5" onClick={(e) => e.stopPropagation()}>
      {chips.map((chip) => {
        const picking = pending?.from === 'chip' && pending.speakerId === chip.speaker.id
        return (
          <button
            key={chip.speaker.id}
            type="button"
            // Not `disabled`: the light theme dims every disabled button alike, and the
            // chip being picked must stand out (a second pick is refused by the pick itself)
            aria-disabled={busy}
            // Keep the keyboard where it was: 1 / 2 and Space go on working
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => { if (!busy) onPick(chip.speaker, 'chip') }}
            title={`${characterName(chip.speaker)} — ${chip.basis}${checking ? ` (press ${chip.key})` : ''}`}
            className={cn(
              CHIP, 'pl-[3px] pr-1.5 max-w-full',
              chip.key === 1 ? 'border-amber-400/80' : 'border-ink-700',
              busy ? (picking ? 'cursor-wait' : 'opacity-40 cursor-default') : 'hover:bg-ink-800',
            )}
          >
            <CharacterFace speaker={chip.speaker} color={colorOf(chip.speaker)} size={20} />
            <span className="font-semibold text-ink-100 truncate">{characterName(chip.speaker)}</span>
            <span className="shrink-0 text-[10px] text-ink-500">{chip.basis}</span>
            {picking ? (
              <Loader2 size={11} className="shrink-0 animate-spin text-amber-300" aria-label="Picking" />
            ) : checking ? (
              <kbd className="shrink-0 rounded-[3px] bg-ink-300 px-1 font-mono text-[9.5px] leading-[14px] text-ink-950">
                {chip.key}
              </kbd>
            ) : null}
          </button>
        )
      })}
      <button
        type="button"
        aria-disabled={busy}
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          if (busy) return
          const r = e.currentTarget.getBoundingClientRect()
          setMenu({ x: r.left, y: r.bottom + 4 })
        }}
        title="Pick from every character of the project"
        className={cn(
          CHIP, 'px-2.5 border-ink-700 text-ink-400',
          busy ? (pending?.from === 'menu' ? 'cursor-wait' : 'opacity-40 cursor-default') : 'hover:bg-ink-800 hover:text-ink-200',
        )}
      >
        {pending?.from === 'menu' && <Loader2 size={11} className="animate-spin text-amber-300" aria-label="Picking" />}
        Someone else…
      </button>
      {menu && (
        <CharacterMenu
          x={menu.x}
          y={menu.y}
          choices={choices}
          colorOf={colorOf}
          onPick={(speaker) => onPick(speaker, 'menu')}
          onNew={onNew}
          onClose={closeMenu}
        />
      )}
    </div>
  )
}
