import { useRef, useEffect, useCallback, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Maximize2, Minimize2, VolumeX, Play, Pause, SkipBack, SkipForward } from 'lucide-react'
import type { Segment, Speaker } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { getSpeakerColor, formatTime, cn } from '@/lib/utils'
import { SubtitleOverlay } from './SubtitleOverlay'

interface VideoPlayerProps {
  videoUrl?: string
  segments: Segment[]
  speakers: Speaker[]
  className?: string
}

export function VideoPlayer({ videoUrl, segments, speakers, className }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [showControls, setShowControls] = useState(true)
  const controlsTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const {
    currentTime, isPlaying, volume, playbackRate, activeSegmentId,
    setCurrentTime, setDuration, setPlaying, setActiveSegment,
    togglePlaying, setPlaybackRate, mutedTrackIds,
  } = useEditorStore()

  const segmentsRef = useRef(segments)
  useEffect(() => {
    segmentsRef.current = segments
  }, [segments])

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
      setCurrentTime(t)
      // Find active segment
      const active = segmentsRef.current.find((s) => t >= s.start_time && t < s.end_time)
      setActiveSegment(active?.id ?? null)
    }
    const onLoadedMetadata = () => setDuration(video.duration)
    const onPlay  = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnded = () => { setPlaying(false); setCurrentTime(0) }

    // Set duration immediately if video is already loaded
    if (video.duration) {
      setDuration(video.duration)
    }

    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('loadedmetadata', onLoadedMetadata)
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    video.addEventListener('ended', onEnded)
    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('loadedmetadata', onLoadedMetadata)
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
      video.removeEventListener('ended', onEnded)
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

  // Volume & Mute BGM
  useEffect(() => {
    if (videoRef.current) {
      const isBgmMuted = mutedTrackIds['__bgm__']
      videoRef.current.volume = isBgmMuted ? 0 : volume
    }
  }, [volume, mutedTrackIds, videoUrl])

  // Playback rate
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = playbackRate
  }, [playbackRate, videoUrl])

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

  // Active segment for subtitle
  const activeSegment = segments.find(
    (s) => currentTime >= s.start_time && currentTime < s.end_time
  )
  const activeSpeaker = speakers.find((sp) => sp.id === activeSegment?.speaker_id)
  const speakerColor = activeSpeaker
    ? (activeSpeaker.color ?? getSpeakerColor(speakers.indexOf(activeSpeaker)))
    : '#7C3AED'

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative bg-black rounded-xl overflow-hidden group select-none',
        'flex items-center justify-center',
        className
      )}
      onMouseMove={resetControlsTimer}
      onMouseLeave={() => isPlaying && setShowControls(false)}
    >
      {/* Video element */}
      {videoUrl ? (
        <video
          ref={videoRef}
          src={videoUrl}
          className="w-full h-full object-contain"
          preload="metadata"
          playsInline
        />
      ) : (
        <div className="flex flex-col items-center justify-center w-full h-full text-center p-8">
          <div className="h-16 w-16 rounded-2xl bg-surface-4 border border-border flex items-center justify-center mb-4">
            <VolumeX size={24} className="text-text-disabled" />
          </div>
          <p className="text-text-muted text-sm">No video available</p>
          <p className="text-text-disabled text-xs mt-1">Upload a video to start editing</p>
        </div>
      )}

      {/* Subtitle overlay */}
      {activeSegment && (
        <SubtitleOverlay
          segment={activeSegment}
          speakerName={activeSpeaker?.name ?? activeSpeaker?.label ?? 'Speaker'}
          speakerColor={speakerColor}
        />
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
