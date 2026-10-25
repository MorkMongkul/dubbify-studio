// src/components/speakers/SameCharacter.tsx
// "Same character?" in the Speakers panel: pairs of characters Movelo thinks
// are one (they share a voice, and maybe a face). Both faces and names, what
// the guess rests on, ▶ to hear one of their lines in the original, Merge (the
// one with more lines stays) and Not the same. Nothing is drawn without suggestions.
import { useEffect, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import type { CharacterSuggestion, Segment, Speaker } from '@/types'
import { CharacterFace } from './CharacterFace'

interface SameCharacterProps {
  suggestions: CharacterSuggestion[]
  speakers: Speaker[]
  /** This episode's lines: ▶ plays one of the character's from the original voices */
  segments: Segment[]
  /** The original voices of this episode, or null while they aren't separated */
  voicesUrl: string | null
  disabled?: boolean
  nameOf: (speaker: Speaker) => string
  colorOf: (speaker: Speaker) => string
  /** Move `source` into `into` */
  onMerge: (source: Speaker, into: Speaker) => void
  onDismiss: (a: string, b: string) => void
}

const CLIP_SECS = 6

/** A confirmed line of the character with the longest original voice (up to CLIP_SECS), else any line with a known span. */
function sampleLine(speakerId: string, segments: Segment[]): { start: number; end: number } | null {
  const spans = segments
    .filter((s) => s.speaker_id === speakerId && s.source_start != null && s.source_end != null && s.source_end > s.source_start)
    .map((s) => ({ confirmed: !!s.who_confirmed, start: s.source_start as number, end: Math.min(s.source_end as number, (s.source_start as number) + CLIP_SECS) }))
  const pool = spans.some((s) => s.confirmed) ? spans.filter((s) => s.confirmed) : spans
  pool.sort((a, b) => (b.end - b.start) - (a.end - a.start))
  return pool[0] ?? null
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
  suggestions, speakers, segments, voicesUrl, disabled, nameOf, colorOf, onMerge, onDismiss,
}: SameCharacterProps) {
  const [playing, setPlaying] = useState<string | null>(null)
  useEffect(() => stopSample, [])

  const pairs = suggestions
    .map((s) => ({ s, a: speakers.find((x) => x.id === s.a), b: speakers.find((x) => x.id === s.b) }))
    .filter((p): p is { s: CharacterSuggestion; a: Speaker; b: Speaker } => !!p.a && !!p.b)
  if (pairs.length === 0) return null

  const toggle = (speaker: Speaker) => {
    const was = playing === speaker.id
    stopSample()
    setPlaying(null)
    if (was || !voicesUrl) return
    const line = sampleLine(speaker.id, segments)
    if (!line) return
    // WebKit ignores a position set before the file's length is known, so it's set on loadedmetadata;
    // the .wav stands in when the .m4a can't be played, as the preview's player does
    const urls = [voicesUrl, voicesUrl.replace('/vocals.m4a?', '/vocals.wav?')]
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

  const who = (speaker: Speaker) => {
    const line = voicesUrl ? sampleLine(speaker.id, segments) : null
    const on = playing === speaker.id
    return (
      <div className="flex items-center gap-1.5 min-w-0 flex-1">
        <CharacterFace speaker={speaker} color={colorOf(speaker)} size={26} />
        <span className="text-[10.5px] text-ink-200 truncate flex-1">{nameOf(speaker)}</span>
        <button
          type="button"
          onClick={() => toggle(speaker)}
          disabled={!line}
          className="shrink-0 h-5 w-5 flex items-center justify-center rounded-full bg-ink-700/60 text-ink-200 hover:text-white hover:bg-ink-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          title={line ? `Hear a line of ${nameOf(speaker)} in the original` : 'No line of this character in this episode to hear'}
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
            <div className="flex items-center gap-2">{who(a)}{who(b)}</div>
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
