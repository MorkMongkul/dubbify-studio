// src/hooks/useStemPeaks.ts
// A stem's audio levels — absolute peaks, PEAKS_PER_SEC per second — decoded
// once per URL, so each music or original-voices piece can draw its own part of
// the waveform at its own volume (components/timeline/StemPieces.tsx).
// Decoded offline, like the voice browser's player: no live AudioContext needed.
import { useEffect, useState } from 'react'
import { peaksFrom } from '@/lib/waveform'

export const PEAKS_PER_SEC = 50

const cache = new Map<string, number[]>()

export function useStemPeaks(url: string | null | undefined): number[] | null {
  const [loaded, setLoaded] = useState<{ url: string; peaks: number[] } | null>(null)

  useEffect(() => {
    if (!url || cache.has(url)) return
    let active = true
    ;(async () => {
      try {
        const res = await fetch(url)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const buffer = await new OfflineAudioContext(1, 1, 44100).decodeAudioData(await res.arrayBuffer())
        const channels = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c))
        const peaks = peaksFrom(channels, buffer.sampleRate, PEAKS_PER_SEC)
        cache.set(url, peaks)
        if (active) setLoaded({ url, peaks })
      } catch (err) {
        // The row keeps its plain stem waveform (TimelineEditor falls back to it)
        console.warn('Stem levels failed for', url, err)
      }
    })()
    return () => { active = false }
  }, [url])

  if (!url) return null
  return cache.get(url) ?? (loaded?.url === url ? loaded.peaks : null)
}
