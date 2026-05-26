// src/components/transcript/TranscriptPanel.tsx
import { useRef, useEffect } from 'react'
import { motion } from 'framer-motion'
import { AlignLeft, CheckCheck, Loader2 } from 'lucide-react'
import type { Segment, Speaker } from '@/types'
import { useEditorStore, useActiveSegmentId } from '@/store/editorStore'
import { useApproveAll } from '@/hooks/useApi'
import { SegmentCardSkeleton } from '@/components/ui/Skeleton'
import { SegmentCard } from './SegmentCard'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

interface TranscriptPanelProps {
  segments: Segment[]
  speakers: Speaker[]
  jobId: string
  isLoading?: boolean
  className?: string
}

export function TranscriptPanel({
  segments, speakers, jobId, isLoading, className
}: TranscriptPanelProps) {
  const activeSegmentId = useActiveSegmentId()
  const { setCurrentTime } = useEditorStore()
  const { mutate: approveAll, isPending: approvingAll } = useApproveAll()
  const scrollRef = useRef<HTMLDivElement>(null)
  const cardRefs  = useRef<Map<string, HTMLDivElement>>(new Map())

  useEffect(() => {
    if (!activeSegmentId) return
    const el = cardRefs.current.get(activeSegmentId)
    if (!el || !scrollRef.current) return
    const { top, bottom } = el.getBoundingClientRect()
    const { top: cTop, bottom: cBottom } = scrollRef.current.getBoundingClientRect()
    if (top < cTop + 40 || bottom > cBottom - 40) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [activeSegmentId])

  const approvedCount = segments.filter((s) => s.is_approved).length
  const totalCount    = segments.length

  const handleApproveAll = () => {
    approveAll(jobId, {
      onSuccess: () => toast.success(`All ${totalCount} segments approved`),
      onError:   () => toast.error('Failed to approve all'),
    })
  }

  const getSpeaker      = (seg: Segment) => speakers.find((sp) => sp.id === seg.speaker_id)
  const getSpeakerIndex = (seg: Segment) => speakers.findIndex((sp) => sp.id === seg.speaker_id)

  return (
    <div
      className={cn('flex flex-col min-w-0 border-l', className)}
      style={{ background: 'var(--color-surface-1)', borderColor: 'var(--color-border)' }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between gap-2 px-3 h-9 border-b shrink-0"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-2)' }}
      >
        <div className="flex items-center gap-1.5">
          <AlignLeft size={12} className="text-white/40" />
          <span className="text-[11px] font-semibold text-white/60 uppercase tracking-wider">Transcript</span>
          {totalCount > 0 && (
            <span className="text-[10px] font-mono text-white/30">
              {approvedCount}/{totalCount}
            </span>
          )}
        </div>

        {totalCount > 0 && approvedCount < totalCount && (
          <button
            className="flex items-center gap-1 text-[11px] text-emerald-400 hover:text-emerald-300 px-1.5 py-0.5 rounded hover:bg-emerald-500/10 transition-colors"
            onClick={handleApproveAll}
            disabled={approvingAll}
          >
            {approvingAll
              ? <Loader2 size={10} className="animate-spin" />
              : <CheckCheck size={11} />
            }
            Approve all
          </button>
        )}
      </div>

      {/* Progress bar */}
      {totalCount > 0 && (
        <div className="px-3 py-1.5 border-b shrink-0" style={{ borderColor: 'var(--color-border)' }}>
          <div className="flex justify-between text-[10px] text-white/25 mb-1">
            <span>Approval</span>
            <span className="font-mono">{Math.round((approvedCount / totalCount) * 100)}%</span>
          </div>
          <div className="h-0.5 rounded-full overflow-hidden" style={{ background: 'var(--color-surface-4)' }}>
            <motion.div
              className="h-full bg-emerald-500 rounded-full"
              animate={{ width: `${(approvedCount / totalCount) * 100}%` }}
              transition={{ duration: 0.35 }}
            />
          </div>
        </div>
      )}

      {/* Segments */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="p-2 space-y-1.5">
            {[...Array(7)].map((_, i) => <SegmentCardSkeleton key={i} />)}
          </div>
        ) : segments.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center p-8">
            <AlignLeft size={22} className="text-white/15 mb-3" />
            <p className="text-[12px] text-white/35">No segments yet</p>
            <p className="text-[11px] text-white/20 mt-1">Process a video to generate transcript</p>
          </div>
        ) : (
          <div className="p-2 space-y-1">
            {segments.map((seg) => (
              <div
                key={seg.id}
                ref={(el) => {
                  if (el) cardRefs.current.set(seg.id, el)
                  else cardRefs.current.delete(seg.id)
                }}
              >
                <SegmentCard
                  segment={seg}
                  speaker={getSpeaker(seg)}
                  speakerIndex={Math.max(0, getSpeakerIndex(seg))}
                  isActive={seg.id === activeSegmentId}
                  onSeek={(t) => setCurrentTime(t)}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
