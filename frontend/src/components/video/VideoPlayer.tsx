import { useRef, useEffect, useCallback, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Maximize2, Minimize2, VolumeX, Play, Pause, SkipBack, SkipForward } from 'lucide-react'
import type { Segment, Speaker } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { formatTime, cn } from '@/lib/utils'

interface VideoPlayerProps {
  videoUrl?: string
  segments: Segment[]
  speakers: Speaker[]
  className?: string
  jobId?: string
  projectId?: string
  jobStatus?: string
}

export function VideoPlayer({ videoUrl, segments, className, jobId, projectId, jobStatus }: VideoPlayerProps) {
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
  const controlsTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  // TTS segment playback — one dubbed clip plays at a time in sync with the video
  const ttsAudioRef      = useRef<HTMLAudioElement | null>(null)
  const ttsActiveSegId   = useRef<string | null>(null)

  const {
    currentTime, isPlaying, volume, playbackRate, activeSegmentId,
    setCurrentTime, setDuration, setPlaying, setActiveSegment,
    togglePlaying, setPlaybackRate, mutedTrackIds,
  } = useEditorStore()

  // Stable refs so event callbacks always see the latest values
  const isPlayingRef    = useRef(isPlaying)
  const volumeRef       = useRef(volume)
  const mutedRef        = useRef(mutedTrackIds)
  useEffect(() => { isPlayingRef.current    = isPlaying },    [isPlaying])
  useEffect(() => { volumeRef.current       = volume },       [volume])
  useEffect(() => { mutedRef.current        = mutedTrackIds }, [mutedTrackIds])

  const segmentsRef = useRef(segments)
  useEffect(() => {
    segmentsRef.current = segments
  }, [segments])

  // ── TTS helpers ───────────────────────────────────────────────────────
  const stopTTS = useCallback(() => {
    if (ttsAudioRef.current) {
      ttsAudioRef.current.pause()
      ttsAudioRef.current.src = ''
      ttsAudioRef.current = null
    }
    ttsActiveSegId.current = null
  }, [])

  const startTTS = useCallback((seg: Segment, videoTime: number) => {
    stopTTS()
    const path = seg.tts_audio_path
    if (!path) return
    // Cache-bust: the backend overwrites the same file path on every
    // re-synthesis, so a version query param is required to avoid the
    // browser serving a stale cached response for a segment's audio.
    const versioned = `${path}?v=${seg.tts_duration_secs ?? 0}`
    const url  = versioned.startsWith('/') ? versioned : `/${versioned}`
    const audio = new Audio(url)
    const offset = Math.max(0, videoTime - seg.start_time)
    if (offset > 0.05) audio.currentTime = offset

    // Auto-fit: speed up the clip so it finishes within the segment window.
    // This keeps dubbed voices in sync with lip movements regardless of how
    // long Gemini took to say the line.
    const segDuration = Math.max(0.1, seg.end_time - seg.start_time)
    const ttsDuration = seg.tts_duration_secs ?? 0
    const videoRate   = videoRef.current?.playbackRate ?? 1
    let rate = videoRate
    if (ttsDuration > 0) {
      // fitRate > 1  → clip is longer than the slot, speed it up
      // fitRate < 1  → clip is shorter, let it play naturally (don't slow down)
      const fitRate = ttsDuration / segDuration
      if (fitRate > 1.05) {
        rate = Math.min(3.5, fitRate * videoRate)
      }
    }
    audio.playbackRate = rate

    const key   = seg.speaker_id ?? '__none__'
    const muted = mutedRef.current[key]
    audio.volume = muted ? 0 : volumeRef.current
    if (isPlayingRef.current) audio.play().catch(() => {})
    ttsAudioRef.current    = audio
    ttsActiveSegId.current = seg.id
  }, [stopTTS])

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

  // Sync video → store + drive TTS playback
  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const onTimeUpdate = () => {
      const t = video.currentTime
      setCurrentTime(t)
      const active = segmentsRef.current.find((s) => t >= s.start_time && t < s.end_time)
      setActiveSegment(active?.id ?? null)

      if (active?.tts_audio_path) {
        if (active.id !== ttsActiveSegId.current) {
          // Entered a new dubbed segment — start its TTS audio
          startTTS(active, t)
        }
        // Keep TTS mute/volume in sync with the current store state
        if (ttsAudioRef.current) {
          const key   = active.speaker_id ?? '__none__'
          const muted = mutedRef.current[key]
          ttsAudioRef.current.volume = muted ? 0 : volumeRef.current
        }
      } else {
        // Left a dubbed segment (or gap between segments)
        if (ttsActiveSegId.current) stopTTS()
      }
    }

    const onLoadedMetadata = () => {
      setDuration(video.duration)
      if (video.videoWidth > 0 && video.videoHeight > 0) {
        setVideoAspectRatio(video.videoWidth / video.videoHeight)
      }
    }
    const onPlay  = () => { setPlaying(true);  ttsAudioRef.current?.play().catch(() => {}) }
    const onPause = () => { setPlaying(false); ttsAudioRef.current?.pause() }
    const onEnded = () => { setPlaying(false); setCurrentTime(0); stopTTS() }
    const onSeeked = () => {
      // After seeking, resync TTS to the new position
      const t      = video.currentTime
      const active = segmentsRef.current.find((s) => t >= s.start_time && t < s.end_time)
      if (active?.tts_audio_path) {
        startTTS(active, t)
        if (!isPlayingRef.current) ttsAudioRef.current?.pause()
      } else {
        stopTTS()
      }
    }

    if (video.duration) setDuration(video.duration)

    video.addEventListener('timeupdate',    onTimeUpdate)
    video.addEventListener('loadedmetadata',onLoadedMetadata)
    video.addEventListener('play',          onPlay)
    video.addEventListener('pause',         onPause)
    video.addEventListener('ended',         onEnded)
    video.addEventListener('seeked',        onSeeked)
    return () => {
      video.removeEventListener('timeupdate',    onTimeUpdate)
      video.removeEventListener('loadedmetadata',onLoadedMetadata)
      video.removeEventListener('play',          onPlay)
      video.removeEventListener('pause',         onPause)
      video.removeEventListener('ended',         onEnded)
      video.removeEventListener('seeked',        onSeeked)
      stopTTS()
    }
  }, [videoUrl, setCurrentTime, setDuration, setPlaying, setActiveSegment, startTTS, stopTTS])

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

  const noVocalsUrl = jobId && projectId ? `/uploads/${projectId}/${jobId}/no_vocals.wav` : null
  const vocalsUrl   = jobId && projectId ? `/uploads/${projectId}/${jobId}/vocals.wav`    : null

  // Separated stems exist from stems_ready onwards (demucs ran in Stage 1)
  const hasStemAudio = !!jobStatus && [
    'stems_ready', 'diarizing', 'transcribing', 'translating',
    'synthesizing', 'mixing', 'completed',
  ].includes(jobStatus)

  // Volume & Mute — BGM, Vocals, and active TTS clip
  useEffect(() => {
    const video = videoRef.current
    const bgm = bgmAudioRef.current
    const vocals = vocalsAudioRef.current

    if (hasStemAudio) {
      if (video) video.volume = 0
      if (bgm) bgm.volume = mutedTrackIds['__bgm__'] ? 0 : volume
      if (vocals) vocals.volume = mutedTrackIds['__vocals__'] ? 0 : volume
    } else {
      if (video) video.volume = volume
      if (bgm) bgm.volume = 0
      if (vocals) vocals.volume = 0
    }
    // Keep active TTS clip in sync with volume/mute changes
    if (ttsAudioRef.current && ttsActiveSegId.current) {
      const seg = segmentsRef.current.find(s => s.id === ttsActiveSegId.current)
      const key = seg?.speaker_id ?? '__none__'
      ttsAudioRef.current.volume = mutedTrackIds[key] ? 0 : volume
    }
  }, [volume, mutedTrackIds, jobStatus, hasStemAudio])

  // Playback rate
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = playbackRate
    if (bgmAudioRef.current) bgmAudioRef.current.playbackRate = playbackRate
    if (vocalsAudioRef.current) vocalsAudioRef.current.playbackRate = playbackRate
    if (ttsAudioRef.current) ttsAudioRef.current.playbackRate = playbackRate
  }, [playbackRate, jobStatus])

  // Synchronize separate audio tracks (BGM & Vocals) with the main video tag
  useEffect(() => {
    const video = videoRef.current
    if (!video || !hasStemAudio) return

    const bgm = bgmAudioRef.current
    const vocals = vocalsAudioRef.current

    const syncTimes = () => {
      const t = video.currentTime
      if (bgm && Math.abs(bgm.currentTime - t) > 0.15) {
        bgm.currentTime = t
      }
      if (vocals && Math.abs(vocals.currentTime - t) > 0.15) {
        vocals.currentTime = t
      }
    }

    const playAudios = () => {
      if (bgm) bgm.play().catch(() => {})
      if (vocals) vocals.play().catch(() => {})
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
    video.addEventListener('seeking', syncTimes)
    video.addEventListener('seeked', syncTimes)
    video.addEventListener('timeupdate', syncTimes)

    // Initial sync
    syncTimes()
    if (isPlaying) {
      playAudios()
    } else {
      pauseAudios()
    }

    return () => {
      video.removeEventListener('play', playAudios)
      video.removeEventListener('pause', pauseAudios)
      video.removeEventListener('seeking', syncTimes)
      video.removeEventListener('seeked', syncTimes)
      video.removeEventListener('timeupdate', syncTimes)
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
            <audio ref={bgmAudioRef} src={noVocalsUrl} preload="auto" />
          )}
          {hasStemAudio && vocalsUrl && (
            <audio ref={vocalsAudioRef} src={vocalsUrl} preload="auto" />
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
                  const t = ratio * (videoRef.current?.duration ?? 0)
                  setCurrentTime(t)
                  if (videoRef.current) videoRef.current.currentTime = t
                }}
                onMouseMove={(e) => {
                  if (e.buttons !== 1) return
                  const rect = e.currentTarget.getBoundingClientRect()
                  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
                  const t = ratio * (videoRef.current?.duration ?? 0)
                  setCurrentTime(t)
                  if (videoRef.current) videoRef.current.currentTime = t
                }}
              >
                <div
                  className="absolute left-0 top-0 h-full bg-gradient-to-r from-purple-500 to-purple-400 rounded-full transition-all"
                  style={{ width: `${videoRef.current?.duration ? (currentTime / videoRef.current.duration) * 100 : 0}%` }}
                />
                <div
                  className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-white shadow opacity-0 group-hover/prog:opacity-100 transition-opacity"
                  style={{ left: `calc(${videoRef.current?.duration ? (currentTime / videoRef.current.duration) * 100 : 0}% - 6px)` }}
                />
              </div>

              {/* Transport row */}
              <div className="flex items-center gap-1.5">
                {/* Skip to segment start */}
                <button
                  className="h-7 w-7 rounded-md flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-colors"
                  onClick={goToSegmentStart}
                  title="Go to segment start"
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
                  title="Go to segment end"
                >
                  <SkipForward size={14} />
                </button>

                {/* Time */}
                <div className="text-[11px] text-white/60 font-mono ml-1">
                  <span className="text-white">{formatTime(currentTime)}</span>
                  <span className="mx-1 opacity-40">/</span>
                  <span>{formatTime(videoRef.current?.duration ?? 0)}</span>
                </div>

                <div className="flex-1" />

                {/* Speed */}
                <select
                  value={playbackRate}
                  onChange={(e) => setPlaybackRate(parseFloat(e.target.value))}
                  className="h-6 px-1.5 rounded bg-black/40 text-[10px] font-mono font-bold text-white/70 hover:text-white border border-white/10 focus:outline-none cursor-pointer transition-all"
                  title="Playback speed"
                >
                  {[0.5, 0.75, 1, 1.25, 1.5, 2].map(r => (
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
