import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Check, Edit3, X, Save, RefreshCw } from 'lucide-react'
import type { Segment, Speaker } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { useUpdateSegment, useApproveSegment } from '@/hooks/useApi'
import { getSpeakerColor, formatTimePrecise, hexToRgba, cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { toast } from 'sonner'

interface SegmentCardProps {
  segment: Segment
  speaker?: Speaker
  speakerIndex: number
  isActive: boolean
  onSeek: (t: number) => void
}

export function SegmentCard({ segment, speaker, speakerIndex, isActive, onSeek }: SegmentCardProps) {
  const { editingSegmentId, setEditingSegment, setActiveSegment } = useEditorStore()
  const isEditing = editingSegmentId === segment.id
  const [draft, setDraft] = useState(segment.khmer_text ?? '')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const { mutate: updateSegment, isPending: saving } = useUpdateSegment()
  const { mutate: approveSegment, isPending: approving } = useApproveSegment()

  const speakerColor = speaker?.color ?? getSpeakerColor(speakerIndex)
  const speakerName  = speaker?.name ?? speaker?.label ?? `Speaker ${speakerIndex + 1}`
  const isApproved   = segment.is_approved

  // Auto-focus textarea when editing starts
  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus()
      textareaRef.current.setSelectionRange(draft.length, draft.length)
    }
  }, [isEditing])

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = el.scrollHeight + 'px'
  }, [draft, isEditing])

  const handleEdit = (e: React.MouseEvent) => {
    e.stopPropagation()
    setDraft(segment.khmer_text ?? '')
    setEditingSegment(segment.id)
  }

  const handleCancel = () => {
    setDraft(segment.khmer_text ?? '')
    setEditingSegment(null)
  }

  const handleSave = () => {
    updateSegment(
      { segmentId: segment.id, data: { khmer_text: draft } },
      {
        onSuccess: () => { setEditingSegment(null); toast.success('Segment saved') },
        onError:   () => toast.error('Failed to save segment'),
      }
    )
  }

  const handleApprove = (e: React.MouseEvent) => {
    e.stopPropagation()
    approveSegment(segment.id, {
      onSuccess: () => toast.success('Segment approved ✓'),
      onError:   () => toast.error('Failed to approve segment'),
    })
  }

  const handleCardClick = () => {
    setActiveSegment(segment.id)
    onSeek(segment.start_time)
  }

  return (
    <motion.div
      layout
      className={cn(
        'relative rounded-xl border transition-all duration-200 overflow-hidden cursor-pointer',
        isActive
          ? 'border-brand/50 bg-brand/8 shadow-glow-sm'
          : 'border-border bg-surface-3 hover:border-border-strong hover:bg-surface-4',
        isApproved && !isActive && 'border-emerald-500/20',
      )}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.15 }}
      onClick={handleCardClick}
    >
      {/* Active glow indicator */}
      {isActive && (
        <motion.div
          className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full"
          style={{ background: speakerColor }}
          layoutId="activeIndicator"
        />
      )}

      {/* Approved indicator */}
      {isApproved && (
        <div className="absolute top-2 right-2">
          <div className="h-5 w-5 rounded-full bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center">
            <Check size={10} className="text-emerald-400" />
          </div>
        </div>
      )}

      <div className="px-3.5 pt-3 pb-2.5">
        {/* Speaker + time row */}
        <div className="flex items-center gap-2 mb-2.5">
          <div
            className="h-5 px-1.5 rounded flex items-center gap-1 text-[10px] font-semibold text-white"
            style={{ background: hexToRgba(speakerColor, 0.75) }}
          >
            <div className="h-1.5 w-1.5 rounded-full bg-white/60" />
            {speakerName}
          </div>
          <button
            className="text-[10px] font-mono text-text-disabled hover:text-brand-400 transition-colors"
            onClick={(e) => { e.stopPropagation(); onSeek(segment.start_time) }}
          >
            {formatTimePrecise(segment.start_time)}
            <span className="mx-0.5 opacity-50">→</span>
            {formatTimePrecise(segment.end_time)}
          </button>
        </div>

        {/* Original text */}
        <div className="mb-2.5">
          <p className="text-[10px] font-medium text-text-disabled uppercase tracking-wider mb-1">Original</p>
          <p className="text-xs text-text-secondary leading-relaxed">{segment.source_text}</p>
        </div>

        {/* Translated text / editor */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <p className="text-[10px] font-medium text-text-disabled uppercase tracking-wider">Translation</p>
            {!isEditing && !isApproved && (
              <button
                className="flex items-center gap-1 text-[10px] text-text-muted hover:text-brand-400 transition-colors"
                onClick={handleEdit}
              >
                <Edit3 size={10} />
                <span>Edit</span>
              </button>
            )}
          </div>

          <AnimatePresence mode="wait">
            {isEditing ? (
              <motion.div
                key="editor"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={(e) => e.stopPropagation()}
              >
                <textarea
                  ref={textareaRef}
                  className="w-full glass-input rounded-lg px-2.5 py-2 text-xs text-text-primary resize-none overflow-hidden focus:outline-none transition-all"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Enter translation…"
                  rows={2}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') handleCancel()
                    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleSave()
                  }}
                />
                <div className="flex gap-1.5 mt-1.5">
                  <Button variant="ghost" size="xs" onClick={handleCancel} icon={<X size={11} />}>
                    Cancel
                  </Button>
                  <Button
                    variant="default"
                    size="xs"
                    loading={saving}
                    onClick={handleSave}
                    icon={<Save size={11} />}
                  >
                    Save
                  </Button>
                </div>
              </motion.div>
            ) : (
              <motion.p
                key="text"
                className={cn(
                  'text-xs leading-relaxed',
                  segment.khmer_text ? 'text-text-primary' : 'text-text-disabled italic'
                )}
              >
                {segment.khmer_text || 'No translation yet'}
              </motion.p>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Footer actions */}
      {!isEditing && (
        <div className="px-3.5 py-2 border-t border-border/50 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {/* Placeholder regenerate button */}
            <button
              className="flex items-center gap-1 text-[10px] text-text-muted hover:text-text-primary transition-colors opacity-50 cursor-not-allowed"
              disabled
              title="Coming soon"
            >
              <RefreshCw size={10} />
              <span>Regenerate</span>
            </button>
          </div>

          {!isApproved ? (
            <Button
              variant="ghost"
              size="xs"
              className="text-emerald-400 hover:text-emerald-300 hover:bg-emerald-500/10"
              onClick={handleApprove}
              loading={approving}
              icon={<Check size={11} />}
            >
              Approve
            </Button>
          ) : (
            <span className="text-[10px] text-emerald-400 font-medium">✓ Approved</span>
          )}
        </div>
      )}
    </motion.div>
  )
}
