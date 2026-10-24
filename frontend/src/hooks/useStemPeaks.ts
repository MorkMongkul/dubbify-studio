// src/hooks/useStemPeaks.ts
// A stem's audio levels — absolute peaks, PEAKS_PER_SEC per second — decoded
// once per URL, so each music or original-voices piece can draw its own part of
// the waveform at its own volume (components/timeline/StemPieces.tsx).
// Decoded offline, like the voice browser's player: no live AudioContext needed.
import { useEffect, useState } from 'react'
import { peaksFrom } from '@/lib/waveform'

export const PEAKS_PER_SEC = 50

const cache = new Map<string, number[]>()
// A decode already on its way: a second row or a remount waits for it instead of decoding again
const inFlight = new Map<string, Promise<number[]>>()

// Decoded at 8 kHz (the preview stems' own rate): levels for drawing need no more,
// and a 44.1 kHz buffer would be 5.5× the memory and the scan.
async function decodeLevels(url: string): Promise<number[]> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const buffer = await new OfflineAudioContext(1, 1, 8000).decodeAudioData(await res.arrayBuffer())
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c))
  return peaksFrom(channels, buffer.sampleRate, PEAKS_PER_SEC)
}

export function useStemPeaks(url: string | null | undefined): number[] | null {
  const [loaded, setLoaded] = useState<{ url: string; peaks: number[] } | null>(null)

  useEffect(() => {
    if (!url || cache.has(url)) return
    let active = true
    let pending = inFlight.get(url)
    if (!pending) {
      pending = decodeLevels(url)
      inFlight.set(url, pending)
      pending.then((peaks) => cache.set(url, peaks), () => {}).finally(() => inFlight.delete(url))
    }
    pending.then(
      (peaks) => { if (active) setLoaded({ url, peaks }) },
      // The row keeps its plain stem waveform (TimelineEditor falls back to it)
      (err) => console.warn('Stem levels failed for', url, err),
    )
    return () => { active = false }
  }, [url])

  if (!url) return null
  return cache.get(url) ?? (loaded?.url === url ? loaded.peaks : null)
}
