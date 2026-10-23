// src/hooks/useScrubAudio.ts
// Hear while dragging (0.3.1): scrubbing the playhead plays short snippets of
// what the export sounds like there — every voice under the playhead from its
// place in its file, at its rate and fade, and the music, dipped under the
// voices when ducking is on. At most one snippet every SNIPPET_EVERY_MS.

import { useCallback, useEffect, useRef, type RefObject } from 'react'
import type { Segment } from '@/types'
import { audibleAt, fadeGainAt, voiceUrl } from '@/lib/clipRules'
import { duckGainAt, duckSpans } from '@/lib/ducking'
import { stemLevelAt, type StemPieceLists } from '@/lib/stemPieces'
import { useEditorStore } from '@/store/editorStore'

const SNIPPET_MS = 120
const SNIPPET_EVERY_MS = 90

interface Options {
  segmentsRef: RefObject<Segment[]>
  /** The music & effects stem's playback URL (null before stems exist). */
  musicUrl: string | null
  duckMusic: boolean
  /** The original voices stem's playback URL, for its pieces switched into the export. */
  voicesUrl?: string | null
  /** The stems' pieces (0.3.2); null/undefined: each stem is one whole piece. */
  pieces?: StemPieceLists | null
}

export function useScrubAudio({ segmentsRef, musicUrl, duckMusic, voicesUrl, pieces }: Options): (t: number) => void {
  const cache = useRef(new Map<string, HTMLAudioElement>())
  const playing = useRef<HTMLAudioElement[]>([])
  const last = useRef(0)
  const stopTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stop = useCallback(() => {
    for (const a of playing.current) a.pause()
    playing.current = []
  }, [])

  useEffect(() => {
    const map = cache.current
    return () => {
      if (stopTimer.current) clearTimeout(stopTimer.current)
      for (const a of map.values()) { a.pause(); a.src = '' }
      map.clear()
    }
  }, [])

  return useCallback((t: number) => {
    const now = performance.now()
    if (now - last.current < SNIPPET_EVERY_MS) return
    last.current = now
    const { volume, mutedTrackIds } = useEditorStore.getState()
    const segs = (segmentsRef.current ?? []).filter((s) => !mutedTrackIds[s.speaker_id ?? '__none__'])
    const audio = (url: string) => {
      let a = cache.current.get(url)
      if (!a) {
        a = new Audio(url)
        a.preload = 'auto'
        ;(a as HTMLAudioElement & { preservesPitch?: boolean }).preservesPitch = true
        cache.current.set(url, a)
      }
      return a
    }
    stop()
    for (const { seg, filePos, rate } of audibleAt(segs, t)) {
      const a = audio(voiceUrl(seg))
      a.currentTime = filePos
      a.playbackRate = rate
      a.volume = Math.max(0, Math.min(1, volume * fadeGainAt(seg, t)))
      playing.current.push(a)
    }
    if (musicUrl && !mutedTrackIds['__bgm__']) {
      const a = audio(musicUrl)
      a.currentTime = t
      a.playbackRate = 1
      a.volume = Math.max(0, Math.min(1, volume * stemLevelAt(pieces, 'music', t, true) * (duckMusic ? duckGainAt(duckSpans(segs), t) : 1)))
      playing.current.push(a)
    }
    // The actors' own voices only where a piece is switched into the export
    const voicesLevel = stemLevelAt(pieces, 'voices', t, true)
    if (voicesUrl && voicesLevel > 0) {
      const a = audio(voicesUrl)
      a.currentTime = t
      a.playbackRate = 1
      a.volume = Math.max(0, Math.min(1, volume * voicesLevel))
      playing.current.push(a)
    }
    for (const a of playing.current) a.play().catch(() => {})
    if (stopTimer.current) clearTimeout(stopTimer.current)
    stopTimer.current = setTimeout(stop, SNIPPET_MS)
  }, [segmentsRef, musicUrl, duckMusic, voicesUrl, pieces, stop])
}
