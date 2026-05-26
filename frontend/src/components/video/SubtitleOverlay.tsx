// src/components/video/SubtitleOverlay.tsx
import { motion, AnimatePresence } from 'framer-motion'
import { hexToRgba } from '@/lib/utils'
import type { Segment } from '@/types'

interface SubtitleOverlayProps {
  segment: Segment
  speakerName: string
  speakerColor: string
}

export function SubtitleOverlay({ segment, speakerName, speakerColor }: SubtitleOverlayProps) {
  const text = segment.khmer_text || segment.source_text

  return (
    <AnimatePresence>
      <motion.div
        key={segment.id}
        className="absolute bottom-16 left-0 right-0 flex flex-col items-center px-6 pointer-events-none"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 4 }}
        transition={{ duration: 0.15 }}
      >
        {/* Speaker label */}
        <div
          className="mb-2 px-2.5 py-0.5 rounded-full text-xs font-semibold text-white"
          style={{ background: hexToRgba(speakerColor, 0.8) }}
        >
          {speakerName}
        </div>

        {/* Subtitle text */}
        <div className="max-w-lg text-center">
          <p
            className="text-white font-semibold text-base leading-snug drop-shadow-[0_2px_4px_rgba(0,0,0,0.9)]"
            style={{
              textShadow: '0 2px 8px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.5)',
            }}
          >
            {text}
          </p>
        </div>
      </motion.div>
    </AnimatePresence>
  )
}
