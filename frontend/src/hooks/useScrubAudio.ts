// src/hooks/useScrubAudio.ts
// Hear while dragging (0.3.1): scrubbing the playhead plays short snippets of
// what the export sounds like there — every voice under the playhead from its
// place in its file, at its rate and fade, and the music, dipped under the
// voices when ducking is on. At most one snippet every SNIPPET_EVERY_MS.

import { useCallback, useEffect, useRef, type RefObject } from 'react'
import type { Segment } from '@/types'
import { audibleAt, fadeGainAt } from '@/lib/clipRules'
import { duckGainAt, duckSpans } from '@/lib/ducking'
import { useEditorStore } from '@/store/editorStore'

const SNIPPET_MS = 120
const SNIPPET_EVERY_MS = 90

interface Options {
  segmentsRef: RefObject<Segment[]>
  /** The music & effects stem's playback URL (null before stems exist). */
  musicUrl: string | null
  duckMusic: boolean
}

export function useScrubAudio({ segmentsRef, musicUrl, duckMusic }: Options): (t: number) => void {
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
      const versioned = `${seg.tts_audio_url}?v=${seg.tts_duration_secs ?? 0}`
      const a = audio(versioned.startsWith('/') ? versioned : `/${versioned}`)
      a.currentTime = filePos
      a.playbackRate = rate
      a.volume = Math.max(0, Math.min(1, volume * fadeGainAt(seg, t)))
      playing.current.push(a)
    }
    if (musicUrl && !mutedTrackIds['__bgm__']) {
      const a = audio(musicUrl)
      a.currentTime = t
      a.playbackRate = 1
      a.volume = Math.max(0, Math.min(1, volume * (duckMusic ? duckGainAt(duckSpans(segs), t) : 1)))
      playing.current.push(a)
    }
    for (const a of playing.current) a.play().catch(() => {})
    if (stopTimer.current) clearTimeout(stopTimer.current)
    stopTimer.current = setTimeout(stop, SNIPPET_MS)
  }, [segmentsRef, musicUrl, duckMusic, stop])
}
