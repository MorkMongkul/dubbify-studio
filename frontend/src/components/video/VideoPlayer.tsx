import { useRef, useEffect, useCallback, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Maximize2, Minimize2, VolumeX } from 'lucide-react'
import type { Segment, Speaker } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { getSpeakerColor, cn } from '@/lib/utils'
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
    currentTime, isPlaying, volume, playbackRate,
    setCurrentTime, setDuration, setPlaying, setActiveSegment,
  } = useEditorStore()

  const segmentsRef = useRef(segments)
  useEffect(() => {
    segmentsRef.current = segments
  }, [segments])

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

  // Volume
  useEffect(() => {
    if (videoRef.current) videoRef.current.volume = volume
  }, [volume, videoUrl])

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
            <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent pointer-events-none" />

            {/* Simplified bottom control bar */}
            <div className="relative pb-3 px-3 flex items-center justify-end gap-2.5 z-10">
              {/* Fullscreen Button */}
              <button
                className="h-8 w-8 rounded-lg bg-black/45 backdrop-blur-sm border border-white/10 flex items-center justify-center text-white/70 hover:text-white hover:bg-black/60 transition-all"
                onClick={toggleFullscreen}
              >
                {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
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
