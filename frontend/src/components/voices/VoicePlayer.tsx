// src/components/voices/VoicePlayer.tsx
// A voice sample shown like a voice message: round play button, the clip's
// real waveform (filling in as it plays, click to jump) and its length. With
// no audio yet it offers to generate a sample instead. One clip plays at a time
// across every player on the page. Give each player key={its url}: a new clip
// starts a fresh player.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Pause, Play, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'

interface Wave { peaks: number[]; duration: number }

const waves = new Map<string, Wave>()
let active: { id: string; stop: () => void } | null = null

/** Average loudness (RMS) per bar, scaled to the loudest bar (decoded once per clip). */
async function loadWave(url: string, bars: number): Promise<Wave> {
  const key = `${url}#${bars}`
  const cached = waves.get(key)
  if (cached) return cached
  const data = await (await fetch(url)).arrayBuffer()
  const buffer = await new OfflineAudioContext(1, 1, 44100).decodeAudioData(data)
  const samples = buffer.getChannelData(0)
  const step = Math.max(1, Math.floor(samples.length / bars))
  const peaks: number[] = []
  for (let i = 0; i < bars; i++) {
    let sum = 0
    let n = 0
    for (let j = i * step; j < Math.min(samples.length, (i + 1) * step); j += 4) {
      sum += samples[j] * samples[j]
      n++
    }
    peaks.push(Math.sqrt(sum / Math.max(1, n)))
  }
  const top = Math.max(...peaks, 1e-6)
  const wave = { peaks: peaks.map((p) => Math.max(0.12, p / top)), duration: buffer.duration }
  waves.set(key, wave)
  return wave
}

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

interface Props {
  id: string
  name: string
  /** Audio to play; null when the voice has no sample yet. */
  url: string | null
  onGenerate: () => void
  generating: boolean
  /** Another voice is generating: generating here would have to wait. */
  busy?: boolean
  /** Start playing as soon as `url` arrives (a sample that was just made). */
  autoPlay?: boolean
  bars?: number
  /** Just the round play button (nothing when there's no sample), for tight spots like a speaker card. */
  compact?: boolean
}

export function VoicePlayer({ id, name, url, onGenerate, generating, busy, autoPlay, bars = 32, compact }: Props) {
  const [wave, setWave] = useState<Wave | null>(null)
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)          // seconds
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const frameRef = useRef(0)
  const boxRef = useRef<HTMLDivElement>(null)

  const stop = useCallback(() => {
    audioRef.current?.pause()
    cancelAnimationFrame(frameRef.current)
    setPlaying(false)
  }, [])

  const play = useCallback((fraction?: number) => {
    if (!url) return
    if (active && active.id !== id) active.stop()
    let audio = audioRef.current
    if (!audio) {
      audio = new Audio(url)
      audio.onended = () => {
        cancelAnimationFrame(frameRef.current)
        setPlaying(false)
        setPosition(0)
      }
      audioRef.current = audio
    }
    if (fraction !== undefined) {
      const seek = (a: HTMLAudioElement) => { a.currentTime = fraction * (a.duration || wave?.duration || 0) }
      if (audio.readyState >= 1) seek(audio)
      else audio.addEventListener('loadedmetadata', () => seek(audio!), { once: true })
    }
    audio.play().then(() => {
      active = { id, stop }
      setPlaying(true)
      cancelAnimationFrame(frameRef.current)
      const follow = () => {
        if (!audioRef.current) return
        setPosition(audioRef.current.currentTime)
        frameRef.current = requestAnimationFrame(follow)
      }
      frameRef.current = requestAnimationFrame(follow)
    }).catch(() => setPlaying(false))
  }, [url, id, stop, wave])

  // Load the waveform once the player is on screen. (A new clip mounts a new
  // player: the panel keys each one by its audio.)
  useEffect(() => {
    if (!url || compact) return
    let cancelled = false
    const load = () => loadWave(url, bars).then((w) => { if (!cancelled) setWave(w) }).catch(() => {})
    const el = boxRef.current
    if (!el || !('IntersectionObserver' in window)) { load(); return () => { cancelled = true } }
    const seen = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { seen.disconnect(); load() }
    }, { rootMargin: '200px' })
    seen.observe(el)
    return () => { cancelled = true; seen.disconnect() }
  }, [url, bars, compact])

  useEffect(() => { if (url && autoPlay) play() }, [url, autoPlay])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    stop()
    if (active?.id === id) active = null
  }, [stop, id])

  if (compact) {
    if (!url) return null
    return (
      <button
        onClick={(e) => { e.stopPropagation(); if (playing) stop(); else play() }}
        aria-label={playing ? `Pause ${name}` : `Play ${name}`}
        title={playing ? 'Pause' : `Listen to ${name}`}
        className="flex items-center justify-center h-6 w-6 shrink-0 rounded-full bg-brand-400 hover:bg-brand-300 text-white transition-colors"
      >
        {playing ? <Pause size={10} fill="currentColor" /> : <Play size={10} fill="currentColor" className="ml-px" />}
      </button>
    )
  }

  if (!url) {
    return (
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <button
          onClick={onGenerate}
          disabled={generating || busy}
          aria-label={`Generate a sample of ${name}`}
          title="Make a sample of this voice saying the preview line (a few seconds)"
          className="flex items-center justify-center h-7 w-7 shrink-0 rounded-full bg-brand-400 hover:bg-brand-300 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {generating ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
        </button>
        <span className="text-[10px] text-white/45 truncate">{generating ? 'Generating…' : 'Generate sample'}</span>
      </div>
    )
  }

  const duration = wave?.duration ?? 0
  const played = duration ? position / duration : 0
  const peaks = wave?.peaks ?? Array.from({ length: bars }, () => 0.12)
  return (
    <div className="flex items-center gap-2 min-w-0 flex-1">
      <button
        onClick={() => (playing ? stop() : play())}
        aria-label={playing ? `Pause ${name}` : `Play ${name}`}
        className="flex items-center justify-center h-7 w-7 shrink-0 rounded-full bg-brand-400 hover:bg-brand-300 text-white transition-colors"
      >
        {playing ? <Pause size={11} fill="currentColor" /> : <Play size={11} fill="currentColor" className="ml-px" />}
      </button>
      <div
        ref={boxRef}
        role="slider"
        aria-label={`${name} position`}
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(position)}
        tabIndex={-1}
        onClick={(e) => {
          const box = e.currentTarget.getBoundingClientRect()
          play(Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)))
        }}
        className="flex items-center gap-[2px] h-7 flex-1 min-w-0 cursor-pointer"
      >
        {peaks.map((p, i) => (
          <span
            key={i}
            className={cn('flex-1 rounded-full transition-colors', (i + 0.5) / peaks.length <= played ? 'bg-brand-400' : 'bg-white/25')}
            style={{ height: `${Math.round(p * 100)}%`, minWidth: 2 }}
          />
        ))}
      </div>
      <span className="text-[10px] text-white/45 tabular-nums shrink-0">
        {playing || position > 0 ? `${clock(position)} / ${clock(duration)}` : clock(duration)}
      </span>
    </div>
  )
}
