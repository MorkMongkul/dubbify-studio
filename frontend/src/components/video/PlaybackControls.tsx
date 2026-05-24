import type { RefObject } from 'react'
import { motion } from 'framer-motion'
import {
  Play, Pause, SkipBack, SkipForward,
  Volume2, VolumeX, Volume1,
} from 'lucide-react'
import { useEditorStore } from '@/store/editorStore'
import { formatTime } from '@/lib/utils'

interface PlaybackControlsProps {
  videoRef: RefObject<HTMLVideoElement | null>
  duration: number
  currentTime: number
}

export function PlaybackControls({ videoRef, duration, currentTime }: PlaybackControlsProps) {
  const { isPlaying, volume, playbackRate, setPlaying, setCurrentTime, setVolume, setPlaybackRate } = useEditorStore()

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0

  const seek = (clientX: number, rect: DOMRect) => {
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    const t = ratio * duration
    setCurrentTime(t)
    if (videoRef.current) videoRef.current.currentTime = t
  }

  const skipBy = (secs: number) => {
    const t = Math.max(0, Math.min(duration, currentTime + secs))
    setCurrentTime(t)
    if (videoRef.current) videoRef.current.currentTime = t
  }

  const VolumeIcon = volume === 0 ? VolumeX : volume < 0.5 ? Volume1 : Volume2

  const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2]
  const nextRate = () => {
    const idx = RATES.indexOf(playbackRate)
    setPlaybackRate(RATES[(idx + 1) % RATES.length])
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Progress bar */}
      <div
        className="relative h-1 rounded-full bg-white/20 cursor-pointer group/prog"
        onClick={(e) => seek(e.clientX, e.currentTarget.getBoundingClientRect())}
        onMouseMove={(e) => {
          if (e.buttons === 1) seek(e.clientX, e.currentTarget.getBoundingClientRect())
        }}
      >
        <motion.div
          className="absolute left-0 top-0 h-full bg-gradient-to-r from-brand to-accent rounded-full"
          style={{ width: `${progress}%` }}
        />
        {/* Thumb */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-white shadow-glow-sm opacity-0 group-hover/prog:opacity-100 transition-opacity"
          style={{ left: `calc(${progress}% - 6px)` }}
        />
      </div>

      {/* Controls row */}
      <div className="flex items-center gap-2">
        {/* Skip back */}
        <button
          className="h-7 w-7 rounded-md flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-colors"
          onClick={() => skipBy(-10)}
        >
          <SkipBack size={14} />
        </button>

        {/* Play/Pause */}
        <motion.button
          className="h-9 w-9 rounded-xl bg-white/90 flex items-center justify-center text-surface-0 hover:bg-white transition-colors shadow-sm"
          onClick={() => setPlaying(!isPlaying)}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.92 }}
        >
          {isPlaying ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" className="ml-0.5" />}
        </motion.button>

        {/* Skip forward */}
        <button
          className="h-7 w-7 rounded-md flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-colors"
          onClick={() => skipBy(10)}
        >
          <SkipForward size={14} />
        </button>

        {/* Time */}
        <div className="text-xs text-white/60 font-mono ml-1">
          <span className="text-white">{formatTime(currentTime)}</span>
          <span className="mx-1">/</span>
          <span>{formatTime(duration)}</span>
        </div>

        <div className="flex-1" />

        {/* Volume */}
        <div className="flex items-center gap-1.5">
          <button
            className="h-7 w-7 rounded-md flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-colors"
            onClick={() => setVolume(volume === 0 ? 0.8 : 0)}
          >
            <VolumeIcon size={14} />
          </button>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={volume}
            onChange={(e) => setVolume(parseFloat(e.target.value))}
            className="w-16 accent-white cursor-pointer opacity-80 hover:opacity-100"
          />
        </div>

        {/* Playback rate */}
        <button
          className="h-6 px-2 rounded text-xs font-mono font-semibold text-white/60 hover:text-white hover:bg-white/10 transition-colors"
          onClick={nextRate}
        >
          {playbackRate}×
        </button>
      </div>
    </div>
  )
}
