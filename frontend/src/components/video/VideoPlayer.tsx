import { useRef, useEffect, useCallback, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Maximize2, Minimize2, VolumeX, Play, Pause, SkipBack, SkipForward } from 'lucide-react'
import type { Segment, Speaker, Overlay, StemPiece, TrackFlagMap } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { formatTime, cn, originalVoicesReplaced } from '@/lib/utils'
import { OverlayCanvas } from './OverlayCanvas'
import { gainFor, resumeAudio, useVoicePlayback } from '@/hooks/useVoicePlayback'
import { frameSecs, isTypingTarget, nextEdge, shuttle } from '@/lib/transport'
import { switchesOf } from '@/lib/trackFlags'
import { syncAction } from '@/lib/playbackSync'
import { duckGainAt, duckSpans } from '@/lib/ducking'
import { pieceAt, setLevel, stemLevelAt } from '@/lib/stemPieces'
import { useStemPieces } from '@/hooks/useApi'

interface VideoPlayerProps {
  videoUrl?: string
  segments: Segment[]
  speakers: Speaker[]
  className?: string
  jobId?: string
  projectId?: string
  jobStatus?: string
  overlays?: Overlay[]
  bgmVolumeDb?: number
  // Changes when the music/voices tracks are made again (Try again)
  stemsVersion?: number
  /** The video's frame rate: one ← / → step (else 1/30 s). */
  frameRate?: number | null
  /** The music & effects dip under the voices (job.duck_music). */
  duckMusic?: boolean
  /** Each voice track's switches: a muted track isn't heard (nor dips the music), a hidden one shows no subtitles. */
  trackFlags?: TrackFlagMap
}

export function VideoPlayer({ videoUrl, segments, className, jobId, projectId, jobStatus, overlays, bgmVolumeDb, stemsVersion, frameRate, duckMusic, trackFlags }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const bgmAudioRef = useRef<HTMLAudioElement>(null)
  const vocalsAudioRef = useRef<HTMLAudioElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [showControls, setShowControls] = useState(true)
  // Most source clips are landscape, but a lot of dubbing work is for Reels/
  // Shorts (9:16 vertical). Default to 16:9 until the real clip's dimensions
  // are known, then size the player to match — a portrait video gets a
  // portrait box instead of shrinking to a sliver inside a landscape frame.
  const [videoAspectRatio, setVideoAspectRatio] = useState(16 / 9)
  // Intrinsic pixel width of the clip. Subtitle font_size is stored in REAL
  // video pixels (that's what the ffmpeg export renders at), so the preview
  // needs this to scale the same size down to the on-screen canvas.
  const [videoPixelWidth, setVideoPixelWidth] = useState(0)
  const controlsTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  // Per-field selectors — this component legitimately re-renders on playhead
  // ticks (progress bar/time display), but it must not also re-render on every
  // unrelated store change (selection, zoom, panel sizes, …).
  const currentTime     = useEditorStore((s) => s.currentTime)
  // Duration comes from the store (written by onLoadedMetadata below), not from
  // videoRef.current.duration: reading a ref during render isn't reactive, so
  // the scrubber and time readout only refreshed when something else happened
  // to re-render — a paused video sat showing 0:00 until the first tick.
  const duration        = useEditorStore((s) => s.duration)
  const isPlaying       = useEditorStore((s) => s.isPlaying)
  const volume          = useEditorStore((s) => s.volume)
  const playbackRate    = useEditorStore((s) => s.playbackRate)
  const activeSegmentId = useEditorStore((s) => s.activeSegmentId)
  const mutedTrackIds   = useEditorStore((s) => s.mutedTrackIds)
  const setCurrentTime  = useEditorStore((s) => s.setCurrentTime)
  const setDuration     = useEditorStore((s) => s.setDuration)
  const setPlaying      = useEditorStore((s) => s.setPlaying)
  const setActiveSegment = useEditorStore((s) => s.setActiveSegment)
  const togglePlaying   = useEditorStore((s) => s.togglePlaying)
  const setPlaybackRate = useEditorStore((s) => s.setPlaybackRate)

  // Stable refs so event callbacks always see the latest values
  const mutedRef        = useRef(mutedTrackIds)
  useEffect(() => { mutedRef.current        = mutedTrackIds }, [mutedTrackIds])

  const segmentsRef = useRef(segments)
  useEffect(() => {
    segmentsRef.current = segments
  }, [segments])

  // What the export plays and shows: muted tracks' voices aren't heard, hidden
  // tracks' subtitles aren't shown (the track headers' switches)
  const voiceSegments = useMemo(
    () => segments.filter((s) => !switchesOf(trackFlags, s.lane_index ?? 0).muted), [segments, trackFlags])
  const subtitleSegments = useMemo(
    () => segments.filter((s) => !switchesOf(trackFlags, s.lane_index ?? 0).hidden), [segments, trackFlags])
  const voiceSegmentsRef = useRef(voiceSegments)
  useEffect(() => { voiceSegmentsRef.current = voiceSegments }, [voiceSegments])

  // The dubbed voices: every clip under the playhead plays its window, as in the export
  useVoicePlayback({ videoRef, segmentsRef: voiceSegmentsRef, isPlaying, playbackRate, volume, mutedRef, videoUrl })

  // ── Transport keys ────────────────────────────────────────────────────
  // J / K / L: back, stop, forward (again: 2×, then 4×). The window can't
  // play video backward, so J steps back in short jumps with the video paused.
  // ← / →: one frame. ↑ / ↓: the previous / next clip edge. Never while typing.
  const shuttleRef = useRef(0)
  const backTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const stopBackStep = useCallback(() => {
    if (backTimerRef.current) { clearInterval(backTimerRef.current); backTimerRef.current = null }
  }, [])
  // Playing again (Space, the play button) ends a J
  useEffect(() => {
    if (isPlaying) { stopBackStep(); if (shuttleRef.current < 0) shuttleRef.current = 0 }
  }, [isPlaying, stopBackStep])
  useEffect(() => stopBackStep, [stopBackStep])

  useEffect(() => {
    const seekTo = (t: number) => {
      const video = videoRef.current
      if (!video) return
      const clamped = Math.max(0, Math.min(video.duration || t, t))
      video.currentTime = clamped
      setCurrentTime(clamped)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return
      if (useEditorStore.getState().settingsOpen) return
      const video = videoRef.current
      if (!video) return
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key
      if (key === 'j' || key === 'k' || key === 'l') {
        e.preventDefault()
        const rate = shuttle(shuttleRef.current, key)
        shuttleRef.current = rate
        stopBackStep()
        if (rate > 0) {
          setPlaybackRate(rate)
          setPlaying(true)
        } else {
          setPlaying(false)
          setPlaybackRate(1)
          if (rate < 0) {
            backTimerRef.current = setInterval(() => {
              const v = videoRef.current
              if (!v || v.currentTime <= 0) { stopBackStep(); return }
              seekTo(v.currentTime - 0.05 * Math.abs(shuttleRef.current))
            }, 50)
          }
        }
      } else if (key === 'ArrowLeft' || key === 'ArrowRight') {
        e.preventDefault()
        stopBackStep()
        shuttleRef.current = 0
        setPlaying(false)
        seekTo(video.currentTime + (key === 'ArrowRight' ? 1 : -1) * frameSecs(frameRate))
      } else if (key === 'ArrowUp' || key === 'ArrowDown') {
        e.preventDefault()
        const edge = nextEdge(segmentsRef.current, video.currentTime, key === 'ArrowDown' ? 1 : -1)
        if (edge != null) seekTo(edge)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [frameRate, setCurrentTime, setPlaying, setPlaybackRate, stopBackStep])

  // Navigate to active segment start
  const goToSegmentStart = useCallback(() => {
    if (activeSegmentId) {
      const seg = segmentsRef.current.find(s => s.id === activeSegmentId)
      if (seg) { setCurrentTime(seg.start_time); return }
    }
    setCurrentTime(0)
  }, [activeSegmentId, setCurrentTime])

  // Navigate to active segment end
  const goToSegmentEnd = useCallback(() => {
    if (activeSegmentId) {
      const seg = segmentsRef.current.find(s => s.id === activeSegmentId)
      if (seg) { setCurrentTime(seg.end_time); return }
    }
  }, [activeSegmentId, setCurrentTime])

  // Sync video → store (the voices follow it in useVoicePlayback)
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const onTimeUpdate = () => {
      const t = video.currentTime
      setCurrentTime(t)
      const active = segmentsRef.current.find((s) => t >= s.start_time && t < s.end_time)
      setActiveSegment(active?.id ?? null)
    }

    const onLoadedMetadata = () => {
      setDuration(video.duration)
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        setVideoAspectRatio(video.videoWidth / video.videoHeight)
        setVideoPixelWidth(video.videoWidth)
      }
    }
    const onPlay  = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnded = () => { setPlaying(false); setCurrentTime(0) }

    if (video.duration) setDuration(video.duration)

    video.addEventListener('timeupdate',    onTimeUpdate)
    video.addEventListener('loadedmetadata',onLoadedMetadata)
    video.addEventListener('play',          onPlay)
    video.addEventListener('pause',         onPause)
    video.addEventListener('ended',         onEnded)
    return () => {
      video.removeEventListener('timeupdate',    onTimeUpdate)
      video.removeEventListener('loadedmetadata',onLoadedMetadata)
      video.removeEventListener('play',          onPlay)
      video.removeEventListener('pause',         onPause)
      video.removeEventListener('ended',         onEnded)
    }
  }, [videoUrl, setCurrentTime, setDuration, setPlaying, setActiveSegment])

  // Store → video (play/pause)
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (isPlaying) {
      video.play().catch(() => {})
    } else {
      video.pause()
    }
  }, [isPlaying, videoUrl])

  // Store → video (seek)
  const lastSyncedTime = useRef<number>(0)
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (Math.abs(video.currentTime - currentTime) > 0.3) {
      video.currentTime = currentTime
      lastSyncedTime.current = currentTime
    }
  }, [currentTime, videoUrl])

  // Prefer the compressed AAC playback stems (~7% of the WAV bytes); fall back
  // to the raw WAVs for jobs separated before the .m4a files existed.
  // Keyed by job id so switching sessions naturally resets to m4a-first.
  const [wavFallbackJobs, setWavFallbackJobs] = useState<Record<string, boolean>>({})
  const stemExt: 'm4a' | 'wav' = jobId && wavFallbackJobs[jobId] ? 'wav' : 'm4a'
  const onStemError = useCallback(() => {
    if (jobId) setWavFallbackJobs((prev) => (prev[jobId] ? prev : { ...prev, [jobId]: true }))
  }, [jobId])
  // Cache-busted by bgm_volume_db and stems_version — the backend overwrites
  // these same files when the BGM gain changes or the tracks are made again,
  // so the URLs need to change too or the browser keeps the old audio.
  const noVocalsUrl = jobId && projectId
    ? `/uploads/${projectId}/${jobId}/no_vocals.${stemExt}?v=${bgmVolumeDb ?? 0}-${stemsVersion ?? 0}`
    : null
  const vocalsUrl   = jobId && projectId ? `/uploads/${projectId}/${jobId}/vocals.${stemExt}?v=${stemsVersion ?? 0}` : null

  // Separated stems exist from stems_ready onwards (demucs ran in Stage 1)
  const hasStemAudio = !!jobStatus && [
    'stems_ready', 'diarizing', 'transcribing', 'translating',
    'synthesizing', 'mixing', 'completed',
  ].includes(jobStatus)

  // Volume & Mute — BGM and Vocals (the voices: useVoicePlayback)
  useEffect(() => {
    const video = videoRef.current
    const bgm = bgmAudioRef.current
    const vocals = vocalsAudioRef.current

    if (hasStemAudio) {
      if (video) video.volume = 0
      if (bgm) bgm.volume = mutedTrackIds['__bgm__'] ? 0 : volume
      if (vocals) vocals.volume = (mutedTrackIds['__vocals__'] ?? originalVoicesReplaced(jobStatus)) ? 0 : volume
    } else {
      if (video) video.volume = volume
      if (bgm) bgm.volume = 0
      if (vocals) vocals.volume = 0
    }
  }, [volume, mutedTrackIds, jobStatus, hasStemAudio])

  // The stems' levels follow the export every frame while playing:
  // - the music: its pieces (silence between them) × the dip under the voices;
  // - the original voices: their pieces — when the track is muted (as after
  //   analysis) only the pieces switched into the export, which the export plays.
  // Before the pieces are known (or with no video length yet) a stem is one whole piece.
  const { data: pieces } = useStemPieces(jobId)
  const duckSpansMemo = useMemo(() => (duckMusic ? duckSpans(voiceSegments) : []), [duckMusic, voiceSegments])
  const vocalsMuted = mutedTrackIds['__vocals__'] ?? originalVoicesReplaced(jobStatus)
  useEffect(() => {
    const bgm = bgmAudioRef.current
    const vocals = vocalsAudioRef.current
    if (!hasStemAudio) return
    const musicBase = mutedTrackIds['__bgm__'] ? 0 : volume
    // Through gain nodes: a piece turned above 0 dB is heard above the file's level, as exported
    const bgmGain = bgm && pieces?.made ? gainFor(bgm) : null
    const vocalsGain = vocals && pieces?.made ? gainFor(vocals) : null
    const live = (list: StemPiece[] | undefined, t: number) => {
      const lv = useEditorStore.getState().livePieceVolume
      const p = lv && list && pieceAt(list, t)
      return p && p.id === lv.id ? 10 ** ((lv.db - p.volume_db) / 20) : 1
    }
    const apply = () => {
      const t = videoRef.current?.currentTime ?? 0
      if (isPlaying) resumeAudio()
      if (bgm) {
        const duck = duckSpansMemo.length ? duckGainAt(duckSpansMemo, t) : 1
        setLevel(bgm, bgmGain, musicBase * stemLevelAt(pieces, 'music', t, vocalsMuted) * live(pieces?.music, t) * duck)
      }
      if (vocals) {
        setLevel(vocals, vocalsGain, volume * stemLevelAt(pieces, 'voices', t, vocalsMuted) * live(pieces?.voices, t))
      }
    }
    apply()
    if (!isPlaying) return
    let raf = requestAnimationFrame(function tick() {
      apply()
      raf = requestAnimationFrame(tick)
    })
    return () => cancelAnimationFrame(raf)
  }, [duckSpansMemo, isPlaying, volume, mutedTrackIds, hasStemAudio, currentTime, pieces, vocalsMuted])

  // Playback rate
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = playbackRate
    if (bgmAudioRef.current) bgmAudioRef.current.playbackRate = playbackRate
    if (vocalsAudioRef.current) vocalsAudioRef.current.playbackRate = playbackRate
  }, [playbackRate, jobStatus])

  // Synchronize separate audio tracks (BGM & Vocals) with the main video tag
  useEffect(() => {
    const video = videoRef.current
    if (!video || !hasStemAudio) return

    const bgm = bgmAudioRef.current
    const vocals = vocalsAudioRef.current

    // A seek puts the stems where the video is; while playing, a small drift
    // is caught up by nudging their speed and only a big one jumps
    // (lib/playbackSync) — a jump on every small drift cut the sound again and again.
    const stems = [bgm, vocals].filter((el): el is HTMLAudioElement => !!el)
    let jumpedAt = 0
    const jumpTo = (t: number, always: boolean) => {
      for (const el of stems) {
        if (always || Math.abs(el.currentTime - t) > 0.04) {
          el.currentTime = t
          jumpedAt = performance.now()
        }
      }
    }
    const onSeek = () => jumpTo(video.currentTime, true)
    const keepInStep = () => {
      if (video.paused) return
      const t = video.currentTime
      const base = video.playbackRate || 1
      for (const el of stems) {
        const action = syncAction(el.currentTime - t, performance.now() - jumpedAt)
        if (action.kind === 'jump') {
          el.currentTime = t
          jumpedAt = performance.now()
        }
        const rate = action.kind === 'nudge' ? base * action.factor : base
        if (Math.abs(el.playbackRate - rate) > 1e-3) el.playbackRate = rate
      }
    }

    const playAudios = () => {
      jumpTo(video.currentTime, false)
      for (const el of stems) el.play().catch(() => {})
    }

    const pauseAudios = () => {
      if (bgm) bgm.pause()
      if (vocals) vocals.pause()
    }

    // Set initial rates
    if (bgm) bgm.playbackRate = playbackRate
    if (vocals) vocals.playbackRate = playbackRate

    video.addEventListener('play', playAudios)
    video.addEventListener('pause', pauseAudios)
    video.addEventListener('seeking', onSeek)
    video.addEventListener('seeked', onSeek)
    video.addEventListener('timeupdate', keepInStep)

    // Initial sync
    jumpTo(video.currentTime, false)
    if (isPlaying) {
      playAudios()
    } else {
      pauseAudios()
    }

    return () => {
      video.removeEventListener('play', playAudios)
      video.removeEventListener('pause', pauseAudios)
      video.removeEventListener('seeking', onSeek)
      video.removeEventListener('seeked', onSeek)
      video.removeEventListener('timeupdate', keepInStep)
      if (bgm) bgm.pause()
      if (vocals) vocals.pause()
    }
  }, [jobStatus, hasStemAudio, isPlaying, playbackRate])

  // Auto-hide controls
  const resetControlsTimer = useCallback(() => {
    setShowControls(true)
    clearTimeout(controlsTimerRef.current)
    if (isPlaying) {
      controlsTimerRef.current = setTimeout(() => setShowControls(false), 3000)
    }
  }, [isPlaying])

  useEffect(() => {
    if (!isPlaying) {
      const handle = requestAnimationFrame(() => setShowControls(true))
      return () => {
        cancelAnimationFrame(handle)
        clearTimeout(controlsTimerRef.current)
      }
    }
    return () => clearTimeout(controlsTimerRef.current)
  }, [isPlaying])

  // Fullscreen
  const toggleFullscreen = async () => {
    const el = containerRef.current
    if (!el) return
    if (!document.fullscreenElement) {
      await el.requestFullscreen()
      setIsFullscreen(true)
    } else {
      await document.exitFullscreen()
      setIsFullscreen(false)
    }
  }

  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', handler)
    return () => document.removeEventListener('fullscreenchange', handler)
  }, [])

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative bg-black rounded-xl overflow-hidden group select-none',
        'flex items-center justify-center',
        className
      )}
      // Sizes the player to the actual clip's aspect ratio (landscape or
      // portrait) rather than forcing every video into a fixed 16:9 box —
      // combined with max-width/max-height on the wrapping element (no
      // explicit width/height), this yields the largest box that fits the
      // available space while preserving the real aspect ratio.
      style={{ aspectRatio: videoAspectRatio }}
      onMouseMove={resetControlsTimer}
      onMouseLeave={() => isPlaying && setShowControls(false)}
    >
      {/* Video element */}
      {videoUrl ? (
        <>
          <video
            ref={videoRef}
            src={videoUrl}
            className="w-full h-full object-contain"
            preload="metadata"
            playsInline
          />
          {hasStemAudio && noVocalsUrl && (
            <audio ref={bgmAudioRef} src={noVocalsUrl} preload="auto" onError={onStemError} />
          )}
          {hasStemAudio && vocalsUrl && (
            <audio ref={vocalsAudioRef} src={vocalsUrl} preload="auto" onError={onStemError} />
          )}
          {jobId && overlays && overlays.length > 0 && (
            <OverlayCanvas overlays={overlays} segments={subtitleSegments} jobId={jobId} currentTime={currentTime} videoPixelWidth={videoPixelWidth} />
          )}
        </>
      ) : (
        <div className="flex flex-col items-center justify-center w-full h-full text-center p-8">
          <div className="h-16 w-16 rounded-2xl bg-surface-4 border border-border flex items-center justify-center mb-4">
            <VolumeX size={24} className="text-text-disabled" />
          </div>
          <p className="text-text-muted text-sm">No video available</p>
          <p className="text-text-disabled text-xs mt-1">Upload a video to start editing</p>
        </div>
      )}

      {/* Controls overlay */}
      <AnimatePresence>
        {showControls && videoUrl && (
          <motion.div
            className="absolute inset-0 flex flex-col justify-end"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
          >
            {/* Gradient scrim */}
            <div className="absolute bottom-0 left-0 right-0 h-24 bg-gradient-to-t from-black/85 to-transparent pointer-events-none" />

            {/* Bottom control bar */}
            <div className="relative z-10 px-3 pb-3 flex flex-col gap-1.5">
              {/* Progress bar */}
              <div
                className="relative h-[3px] rounded-full bg-white/20 cursor-pointer group/prog"
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect()
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
                  const t = ratio * duration
                  setCurrentTime(t)
                  if (videoRef.current) videoRef.current.currentTime = t
                }}
                onMouseMove={(e) => {
                  if (e.buttons !== 1) return
                  const rect = e.currentTarget.getBoundingClientRect()
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
                  const t = ratio * duration
                  setCurrentTime(t)
                  if (videoRef.current) videoRef.current.currentTime = t
                }}
              >
                <div
                  className="absolute left-0 top-0 h-full bg-gradient-to-r from-brand-500 to-brand-400 rounded-full transition-all"
                  style={{ width: `${duration ? (currentTime / duration) * 100 : 0}%` }}
                />
                <div
                  className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-white shadow opacity-0 group-hover/prog:opacity-100 transition-opacity"
                  style={{ left: `calc(${duration ? (currentTime / duration) * 100 : 0}% - 6px)` }}
                />
              </div>

              {/* Transport row */}
              <div className="flex items-center gap-1.5">
                {/* Skip to segment start */}
                <button
                  className="h-7 w-7 rounded-md flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-colors"
                  onClick={goToSegmentStart}
                  title="Go to the start of the line"
                >
                  <SkipBack size={14} />
                </button>

                {/* Play / Pause */}
                <motion.button
                  className="h-8 w-8 rounded-full bg-white flex items-center justify-center text-neutral-900 hover:bg-white/90 transition-colors shadow-sm"
                  onClick={togglePlaying}
                  whileHover={{ scale: 1.05 }}
                  whileTap={{ scale: 0.92 }}
                >
                  {isPlaying
                    ? <Pause size={14} fill="currentColor" />
                    : <Play  size={14} fill="currentColor" className="ml-0.5" />}
                </motion.button>

                {/* Skip to segment end */}
                <button
                  className="h-7 w-7 rounded-md flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-colors"
                  onClick={goToSegmentEnd}
                  title="Go to the end of the line"
                >
                  <SkipForward size={14} />
                </button>

                {/* Time */}
                <div className="text-[11px] text-white/60 font-mono ml-1">
                  <span className="text-white">{formatTime(currentTime)}</span>
                  <span className="mx-1 opacity-40">/</span>
                  <span>{formatTime(duration)}</span>
                </div>

                <div className="flex-1" />

                {/* Speed */}
                <select
                  value={playbackRate}
                  onChange={(e) => setPlaybackRate(parseFloat(e.target.value))}
                  className="h-6 px-1.5 rounded bg-black/40 text-[10px] font-mono font-bold text-white/70 hover:text-white border border-white/10 focus:outline-none cursor-pointer transition-all"
                  title="Playback speed"
                >
                  {[0.5, 0.75, 1, 1.25, 1.5, 2, 4].map(r => (
                    <option key={r} value={r}>{r}×</option>
                  ))}
                </select>

                {/* Fullscreen */}
                <button
                  className="h-7 w-7 rounded-md flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-colors"
                  onClick={toggleFullscreen}
                >
                  {isFullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Click to play/pause */}
      {videoUrl && (
        <div
          className="absolute inset-0 cursor-pointer"
          style={{ bottom: showControls ? 48 : 0 }}
          onClick={() => setPlaying(!isPlaying)}
        />
      )}
    </div>
  )
}
