// src/components/speakers/SameCharacter.tsx
// "Same character?" in the Speakers panel: pairs of characters Movelo thinks
// are one (they share a voice, and maybe a face). Both faces and names, what
// the guess rests on, ▶ to hear one of their lines in the original — from any
// episode, since the open one may not have both (the server picks the line) —
// Merge (the one with more lines stays) and Not the same. Nothing is drawn
// without suggestions.
import { useEffect, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import type { CharacterSuggestion, SampleLine, Speaker } from '@/types'
import { CharacterFace } from './CharacterFace'

interface SameCharacterProps {
  suggestions: CharacterSuggestion[]
  speakers: Speaker[]
  disabled?: boolean
  nameOf: (speaker: Speaker) => string
  colorOf: (speaker: Speaker) => string
  /** Move `source` into `into` */
  onMerge: (source: Speaker, into: Speaker) => void
  onDismiss: (a: string, b: string) => void
}

let audio: HTMLAudioElement | null = null
let stopListener: (() => void) | null = null
function stopSample() {
  audio?.pause()
  audio = null
  stopListener?.()
  stopListener = null
}

export function SameCharacter({
  suggestions, speakers, disabled, nameOf, colorOf, onMerge, onDismiss,
}: SameCharacterProps) {
  const [playing, setPlaying] = useState<string | null>(null)
  useEffect(() => stopSample, [])

  const pairs = suggestions
    .map((s) => ({ s, a: speakers.find((x) => x.id === s.a), b: speakers.find((x) => x.id === s.b) }))
    .filter((p): p is { s: CharacterSuggestion; a: Speaker; b: Speaker } => !!p.a && !!p.b)
  if (pairs.length === 0) return null

  const toggle = (speaker: Speaker, line: SampleLine | null | undefined) => {
    const was = playing === speaker.id
    stopSample()
    setPlaying(null)
    if (was || !line) return
    // WebKit ignores a position set before the file's length is known, so it's set on loadedmetadata;
    // the .wav stands in when the .m4a can't be played, as the preview's player does
    const urls = [line.url, line.url.replace('/vocals.m4a?', '/vocals.wav?')]
    const open = (k: number) => {
      const el = new Audio()
      audio = el
      const done = () => { if (audio === el) { stopSample(); setPlaying(null) } }
      el.addEventListener('timeupdate', () => { if (el.currentTime >= line.end) done() })
      el.addEventListener('ended', done)
      el.addEventListener('error', () => {
        if (audio !== el) return
        if (k + 1 < urls.length && urls[k + 1] !== urls[k]) { el.pause(); open(k + 1) } else done()
      })
      el.addEventListener('loadedmetadata', () => {
        el.currentTime = line.start
        el.play().then(() => { if (audio === el) setPlaying(speaker.id) }).catch(done)
      }, { once: true })
      el.preload = 'auto'
      el.src = urls[k]
      el.load()
    }
    open(0)
  }

  const who = (speaker: Speaker, line: SampleLine | null | undefined) => {
    const on = playing === speaker.id
    return (
      <div className="flex items-center gap-1.5 min-w-0 flex-1">
        <CharacterFace speaker={speaker} color={colorOf(speaker)} size={26} />
        <span className="text-[10.5px] text-ink-200 truncate flex-1">{nameOf(speaker)}</span>
        <button
          type="button"
          onClick={() => toggle(speaker, line)}
          disabled={!line}
          className="shrink-0 h-5 w-5 flex items-center justify-center rounded-full bg-ink-700/60 text-ink-200 hover:text-white hover:bg-ink-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          title={line ? `Hear a line of ${nameOf(speaker)} in the original` : 'No line of this character to hear yet (no episode with separated voices)'}
          aria-label={`${on ? 'Stop' : 'Play'} a line of ${nameOf(speaker)}`}
        >
          {on ? <Pause size={9} /> : <Play size={9} className="ml-px" />}
        </button>
      </div>
    )
  }

  return (
    <div className="p-2 rounded bg-ink-800/30 border border-amber-400/30 space-y-2">
      <p className="text-[10px] font-semibold text-amber-300/90">Same character?</p>
      {pairs.map(({ s, a, b }) => {
        // The one with more lines in the project stays (the first of the pair when they tie)
        const keep = s.b_lines > s.a_lines ? b : a
        const other = keep === a ? b : a
        return (
          <div key={`${s.a}:${s.b}`} className="space-y-1.5 pt-1.5 border-t border-ink-800/60 first-of-type:border-t-0 first-of-type:pt-0">
            <div className="flex items-center gap-2">{who(a, s.a_sample)}{who(b, s.b_sample)}</div>
            <p className="text-[9px] text-ink-500">{s.reason === 'same voice and face' ? 'Same voice and face' : 'Same voice'}</p>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => { stopSample(); setPlaying(null); onMerge(other, keep) }}
                disabled={disabled}
                title={`Move all of ${nameOf(other)}'s lines to ${nameOf(keep)}, in every episode`}
                className="flex-1 py-1 rounded bg-brand-600 hover:bg-brand-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-[10px] font-semibold transition-colors"
              >
                Merge
              </button>
              <button
                type="button"
                onClick={() => onDismiss(s.a, s.b)}
                className="flex-1 py-1 rounded bg-ink-800 hover:bg-ink-700 text-ink-300 text-[10px] transition-colors"
              >
                Not the same
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
