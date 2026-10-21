// src/components/voices/VoiceBrowser.tsx
// Choosing a voice: search, quick filters, then every voice grouped like the
// Voices tab, each with its tags, a player and which speakers already use it.
// It fills its container: the Speakers panel slides it over its list, and the
// transcript shows it in a window (a new speaker, a single line).
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { Check, Search, Sparkles, X } from 'lucide-react'
import { toast } from 'sonner'
import type { Voice } from '@/types'
import { usePreviewVoice } from '@/hooks/useApi'
import { SAMPLE_LINE, VOICE_FILTERS, filterVoices, groupVoices, type VoiceFilterKey } from '@/lib/voiceTags'
import { cn } from '@/lib/utils'
import { VoicePlayer } from './VoicePlayer'
import { VoiceTags } from './VoiceTags'

interface VoiceBrowserProps {
  voices: Voice[]
  /** The voice in use now (null: the automatic choice below). */
  currentVoiceId: string | null
  /** {voice id: names of the other speakers using it} (lib/voiceTags voiceUsers). */
  usedBy: Record<string, string[]>
  /** The choice that leaves it to the app, e.g. "Choose for me" or the speaker's voice. */
  auto: { label: string; hint?: string }
  onChoose: (voiceId: string | null) => void
  /** Esc: back without choosing. */
  onClose: () => void
  header?: ReactNode
  className?: string
}

export function VoiceBrowser({ voices, currentVoiceId, usedBy, auto, onChoose, onClose, header, className }: VoiceBrowserProps) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<VoiceFilterKey>('all')
  const visible = useMemo(() => filterVoices(voices, query, filter), [voices, query, filter])
  const groups = useMemo(() => groupVoices(visible), [visible])
  const filtering = query.trim() !== '' || filter !== 'all'

  // Samples made here for voices that had none (some of the user's own)
  const previewVoice = usePreviewVoice()
  const [samples, setSamples] = useState<Record<string, string>>({})
  const [making, setMaking] = useState<string | null>(null)
  const [justMade, setJustMade] = useState<string | null>(null)
  const madeUrls = useRef<string[]>([])
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    const urls = madeUrls.current     // one array, filled as samples are made
    return () => {
      mounted.current = false
      urls.forEach((u) => URL.revokeObjectURL(u))
    }
  }, [])
  // Players here stop players of the same voice elsewhere (e.g. the Speakers panel's browser)
  const playerScope = useId()

  const sampleUrl = (v: Voice) =>
    samples[v.id] ?? (v.reference_audio_url ? `${v.reference_audio_url}?v=${v.seed}` : null)

  const makeSample = async (v: Voice) => {
    setMaking(v.id)
    try {
      const blob = await previewVoice.mutateAsync({ id: v.id, text: SAMPLE_LINE })
      if (!mounted.current) return     // closed meanwhile: nothing would free it
      const url = URL.createObjectURL(blob)
      madeUrls.current.push(url)
      setSamples((prev) => ({ ...prev, [v.id]: url }))
      setJustMade(v.id)
    } catch {
      toast.error("Couldn't make a sample. The voice service may be starting up; try again in a moment.")
    } finally {
      if (mounted.current) setMaking(null)
    }
  }

  // Open where the current voice is
  const listRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    listRef.current?.querySelector('[data-current="true"]')?.scrollIntoView({ block: 'center' })
  }, [])

  const autoCurrent = currentVoiceId === null
  return (
    <div
      className={cn('flex flex-col min-h-0', className)}
      // Keys typed here are for the browser: the editor's shortcuts (split,
      // delete, play) and a window around it (Esc) mustn't see them.
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Escape') onClose()
      }}
      // After a click on a filter, a player or a button, typing goes on into
      // the search (in the desktop app a clicked button doesn't keep focus,
      // so keys would reach the editor instead)
      onClick={(e) => {
        if (e.target !== searchRef.current) searchRef.current?.focus({ preventScroll: true })
      }}
    >
      {header}

      {/* Search + quick filters */}
      <div className="px-3 pt-2.5 pb-2 space-y-2 border-b border-ink-800/60 shrink-0">
        <div className="relative flex items-center">
          <Search size={12} className="absolute left-2.5 text-ink-500 pointer-events-none" />
          <input
            ref={searchRef}
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search name, man, 30s, cold, CEO…"
            aria-label="Search voices"
            className="w-full h-8 pr-7 rounded-md border bg-ink-950/60 border-ink-700/70 text-[11.5px] text-ink-100 placeholder-ink-600 focus:outline-none focus:border-brand-500/60"
            style={{ paddingLeft: 28 }}
          />
          {query && (
            <button
              onClick={() => setQuery('')}
              aria-label="Clear search"
              className="absolute right-1.5 flex items-center justify-center h-5 w-5 rounded text-ink-500 hover:text-white hover:bg-white/10"
            >
              <X size={11} />
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          {VOICE_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              aria-pressed={filter === f.key}
              className={cn(
                'h-6 px-2.5 rounded-full border text-[10.5px] font-medium transition-colors',
                filter === f.key
                  ? 'bg-brand-500 border-brand-500 text-white'
                  : 'border-ink-700/70 text-ink-400 hover:text-white hover:border-ink-600',
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-3">
        {!query.trim() && (
          <button
            data-current={autoCurrent}
            onClick={() => onChoose(null)}
            className={cn(
              'w-full flex items-start gap-2 rounded-lg border p-2.5 text-left transition-colors',
              autoCurrent ? 'border-brand-400/60 bg-brand-500/[0.07]' : 'border-ink-800/80 hover:border-ink-600',
            )}
          >
            <Sparkles size={13} className="text-brand-300 mt-0.5 shrink-0" />
            <span className="min-w-0 flex-1">
              <span className="block text-[12px] font-semibold text-white">{auto.label}</span>
              {auto.hint && <span className="block text-[10px] text-ink-500 leading-snug mt-0.5">{auto.hint}</span>}
            </span>
            {autoCurrent && <Check size={13} className="text-brand-300 shrink-0 mt-0.5" />}
          </button>
        )}

        {groups.map((group) => (
          <section key={group.key}>
            <h4 className="text-[10.5px] text-ink-500 font-medium mb-1.5">
              {group.label} <span className="text-ink-600">· {group.voices.length}</span>
            </h4>
            <div className="space-y-1.5">
              {group.voices.map((v) => {
                const current = v.id === currentVoiceId
                const users = usedBy[v.id] ?? []
                return (
                  <div
                    key={v.id}
                    data-current={current}
                    className={cn(
                      'rounded-lg border p-2.5 transition-colors',
                      current ? 'border-brand-400/60 bg-brand-500/[0.07]' : 'hover:border-white/[0.14]',
                    )}
                    style={current ? undefined : { borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}
                  >
                    <div className="flex items-start gap-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-[12.5px] font-semibold text-white truncate">
                          {v.name}
                          {!v.builtin && <span className="ml-1.5 text-[8px] px-1.5 py-0.5 rounded-full bg-white/10 text-white/70 uppercase tracking-wide font-semibold align-middle">Yours</span>}
                        </p>
                        {users.length > 0 && (
                          <p className="text-[10px] text-amber-300/90 mt-0.5 truncate" title={`Already used by ${users.join(', ')}`}>
                            Used by {users.join(', ')}
                          </p>
                        )}
                      </div>
                      {current ? (
                        <span className="shrink-0 inline-flex items-center gap-1 h-6 px-1.5 text-[10.5px] font-semibold text-brand-300">
                          <Check size={11} /> Current
                        </span>
                      ) : (
                        <button
                          onClick={() => onChoose(v.id)}
                          className="shrink-0 h-6 px-3 rounded-md bg-brand-500 hover:bg-brand-400 text-white text-[10.5px] font-semibold transition-colors"
                          aria-label={`Use ${v.name}`}
                        >
                          Use
                        </button>
                      )}
                    </div>
                    <VoiceTags voice={v} className="mt-1.5" />
                    <div className="flex items-center mt-2">
                      <VoicePlayer
                        key={sampleUrl(v) ?? 'none'}
                        id={`${playerScope}:${v.id}`}
                        name={v.name}
                        url={sampleUrl(v)}
                        onGenerate={() => makeSample(v)}
                        generating={making === v.id}
                        busy={making !== null}
                        autoPlay={justMade === v.id}
                        bars={28}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        ))}

        {groups.length === 0 && (
          <div className="text-center py-8">
            <p className="text-[11.5px] text-ink-400">
              {query.trim() ? <>No voice matches “{query.trim()}”</> : 'No voices here yet'}
            </p>
            {filtering && (
              <button
                onClick={() => { setQuery(''); setFilter('all') }}
                className="text-[10.5px] text-brand-300 hover:text-brand-200 mt-1.5 transition-colors"
              >
                Clear search and filters
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
