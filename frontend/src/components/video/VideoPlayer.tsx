import { useRef, useEffect, useCallback, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Maximize2, Minimize2, VolumeX, Play, Pause, SkipBack, SkipForward } from 'lucide-react'
import type { Segment, Speaker, StemPiece, TrackFlagMap } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { formatTime, cn, originalVoicesReplaced } from '@/lib/utils'
import { MediaCanvas } from './MediaCanvas'
import { heldByEngine, usePreviewAudio, type Stem } from '@/hooks/usePreviewAudio'
import { frameSecs, isTypingTarget, nextEdge, seekVideoTo, shuttle } from '@/lib/transport'
import { switchesOf } from '@/lib/trackFlags'
import { duckGainAt, duckSpans } from '@/lib/ducking'
import { pieceAt, stemLevelAt } from '@/lib/stemPieces'
import { useStemPieces } from '@/hooks/useApi'

interface VideoPlayerProps {
  videoUrl?: string
  segments: Segment[]
  speakers: Speaker[]
  className?: string
  jobId?: string
  projectId?: string
  jobStatus?: string
  bgmVolumeDb?: number
  // Changes when the music/voices tracks are made again (Try again)
  stemsVersion?: number
  /** The video's frame rate: one ← / → step (else 1/30 s). */
  frameRate?: number | null
  /** The music & effects dip under the voices (job.duck_music). */
  duckMusic?: boolean
  /** Each voice track's switches: a muted track isn't heard (nor dips the music). */
  trackFlags?: TrackFlagMap
}

export function VideoPlayer({ videoUrl, segments, className, jobId, projectId, jobStatus, bgmVolumeDb, stemsVersion, frameRate, duckMusic, trackFlags }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [showControls, setShowControls] = useState(true)
  // Most source clips are landscape, but a lot of dubbing work is for Reels/
  // Shorts (9:16 vertical). Default to 16:9 until the real clip's dimensions
  // are known, then size the player to match — a portrait video gets a
  // portrait box instead of shrinking to a sliver inside a landscape frame.
  const [videoAspectRatio, setVideoAspectRatio] = useState(16 / 9)
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

  // The last time the video itself reported (the store follows it while playing)
  const fromVideo = useRef<number | null>(null)
  const segmentsRef = useRef(segments)
  useEffect(() => {
    segmentsRef.current = segments
  }, [segments])

  // What the export plays: muted tracks' voices aren't heard (the track headers' switches)
  const voiceSegments = useMemo(
    () => segments.filter((s) => !switchesOf(trackFlags, s.lane_index ?? 0).muted), [segments, trackFlags])

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

  // Sync video → store
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const onTimeUpdate = () => {
      const t = video.currentTime
      fromVideo.current = t
      setCurrentTime(t)
      const active = segmentsRef.current.find((s) => t >= s.start_time && t < s.end_time)
      setActiveSegment(active?.id ?? null)
    }

    const onLoadedMetadata = () => {
      setDuration(video.duration)
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        setVideoAspectRatio(video.videoWidth / video.videoHeight)
        useEditorStore.getState().setVideoSize({ width: video.videoWidth, height: video.videoHeight })
      }
    }
    const onPlay  = () => setPlaying(true)
    // Not when the preview's sound engine holds the picture to start it again in step
    const onPause = () => { if (!heldByEngine(video)) setPlaying(false) }
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

  // Store → video (play/pause): usePreviewAudio, which starts the picture with the sound

  // Store → video (seek): only when the playhead was moved (a click, a key, a
  // clip), and exactly there — never to catch up with a time the video itself
  // reported, read back late after a busy moment (lib/transport.seekVideoTo)
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const to = seekVideoTo(currentTime, fromVideo.current, video.currentTime)
    if (to != null) video.currentTime = to
  }, [currentTime, videoUrl])

  // The stems' files, best first: the compressed AAC copies (~7% of the WAV
  // bytes), then the raw WAVs for jobs separated before the .m4a files existed.
  // Cache-busted by bgm_volume_db and stems_version — the backend overwrites
  // these same files when the BGM gain changes or the tracks are made again,
  // so the URLs need to change too or the browser keeps the old audio.
  const stemFiles = useMemo(() => {
    if (!jobId || !projectId) return null
    const dir = `/uploads/${projectId}/${jobId}`
    const music = `?v=${bgmVolumeDb ?? 0}-${stemsVersion ?? 0}`
    const voices = `?v=${stemsVersion ?? 0}`
    return {
      music: [`${dir}/no_vocals.m4a${music}`, `${dir}/no_vocals.wav${music}`],
      voices: [`${dir}/vocals.m4a${voices}`, `${dir}/vocals.wav${voices}`],
    }
  }, [jobId, projectId, bgmVolumeDb, stemsVersion])

  // Separated stems exist from stems_ready onwards (demucs ran in Stage 1)
  const hasStemAudio = !!jobStatus && [
    'stems_ready', 'diarizing', 'transcribing', 'translating',
    'synthesizing', 'mixing', 'completed',
  ].includes(jobStatus)

  // The video's own sound is heard only until the episode is separated; then
  // the stems and the voices are (usePreviewAudio)
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    // Muted, not just silent: Safari then doesn't ready its own sound output before the picture starts
    video.muted = hasStemAudio
    video.volume = hasStemAudio ? 0 : volume
  }, [volume, hasStemAudio, videoUrl])

  // The stems' levels follow the export (usePreviewAudio reads them every frame):
  // - the music: its pieces (silence between them) × the dip under the voices;
  // - the original voices: their pieces — when the track is muted (as after
  //   analysis) only the pieces switched into the export, which the export plays.
  // Before the pieces are known (or with no video length yet) a stem is one whole piece.
  // Through gain nodes: a piece turned above 0 dB is heard above the file's level, as exported.
  const { data: pieces } = useStemPieces(jobId)
  const duckSpansMemo = useMemo(() => (duckMusic ? duckSpans(voiceSegments) : []), [duckMusic, voiceSegments])
  const vocalsMuted = mutedTrackIds['__vocals__'] ?? originalVoicesReplaced(jobStatus)
  const stemLevel = useRef<(stem: Stem, t: number) => number>(() => 0)
  useEffect(() => {
    const musicBase = mutedTrackIds['__bgm__'] ? 0 : volume
    const live = (list: StemPiece[] | undefined, t: number) => {
      const lv = useEditorStore.getState().livePieceVolume
      const p = lv && list && pieceAt(list, t)
      return p && p.id === lv.id ? 10 ** ((lv.db - p.volume_db) / 20) : 1
    }
    stemLevel.current = (stem, t) => stem === 'music'
      ? musicBase * stemLevelAt(pieces, 'music', t, vocalsMuted) * live(pieces?.music, t)
        * (duckSpansMemo.length ? duckGainAt(duckSpansMemo, t) : 1)
      : volume * stemLevelAt(pieces, 'voices', t, vocalsMuted) * live(pieces?.voices, t)
  }, [duckSpansMemo, volume, mutedTrackIds, pieces, vocalsMuted])

  // Everything heard: the voices (every clip under the playhead plays its window,
  // as in the export), the music & effects and the original voices — on one clock
  usePreviewAudio({
    videoRef, videoUrl, isPlaying, playbackRate, volume,
    segments: voiceSegments, muted: mutedTrackIds, stems: hasStemAudio ? stemFiles : null, stemLevel,
  })

  // Playback rate
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = playbackRate
  }, [playbackRate, jobStatus])

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
            // Loaded ahead, like the sound: the first play doesn't stall to load the picture
            preload="auto"
            playsInline
          />
          {/* The episode's media, drawn over the picture with its transparency */}
          {jobId && <MediaCanvas jobId={jobId} projectId={projectId} videoRef={videoRef} containerRef={containerRef} />}
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
            className="absolute inset-0 z-40 flex flex-col justify-end pointer-events-none"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
          >
            {/* Gradient scrim */}
            <div className="absolute bottom-0 left-0 right-0 h-24 bg-gradient-to-t from-black/85 to-transparent pointer-events-none" />

            {/* Bottom control bar */}
            <div className="relative z-10 px-3 pb-3 flex flex-col gap-1.5 pointer-events-auto">
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
