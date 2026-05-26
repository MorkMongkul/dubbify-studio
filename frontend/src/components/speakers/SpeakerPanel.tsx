// src/components/speakers/SpeakerPanel.tsx
import { useState } from 'react'
import { motion } from 'framer-motion'
import { Users, Edit2, Check, X } from 'lucide-react'
import type { Speaker, Segment } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import { useUpdateSpeaker } from '@/hooks/useApi'
import { getSpeakerColor, cn } from '@/lib/utils'
import { toast } from 'sonner'

interface SpeakerPanelProps {
  speakers: Speaker[]
  projectId?: string
  className?: string
  segments?: Segment[]
}

const PRESET_COLORS = [
  '#7C3AED', '#2563EB', '#059669', '#D97706',
  '#DC2626', '#DB2777', '#0891B2', '#65A30D',
]

export function SpeakerPanel({ speakers, className, segments }: SpeakerPanelProps) {
  const { selectedSpeakerId, setSelectedSpeaker } = useEditorStore()
  const { mutate: updateSpeaker } = useUpdateSpeaker()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')

  const startEdit = (sp: Speaker) => {
    setEditingId(sp.id)
    setEditName(sp.name ?? sp.label ?? '')
  }

  const cancelEdit = () => { setEditingId(null); setEditName('') }

  const saveEdit = (sp: Speaker) => {
    updateSpeaker(
      { speakerId: sp.id, data: { name: editName.trim() || sp.label } },
      {
        onSuccess: () => { cancelEdit(); toast.success('Speaker name updated') },
        onError:   () => toast.error('Failed to update speaker'),
      }
    )
  }

  const handleColorChange = (sp: Speaker, color: string) => {
    updateSpeaker(
      { speakerId: sp.id, data: { color } },
      { onSuccess: () => toast.success('Color updated') }
    )
  }

  return (
    <div className={cn('flex flex-col bg-surface-2 border-r border-border', className)}>
      {/* Header */}
      <div className="px-4 py-3 border-b border-border shrink-0 flex items-center gap-2">
        <Users size={14} className="text-text-muted" />
        <span className="text-xs font-semibold text-text-primary uppercase tracking-wider">Speakers</span>
        <span className="ml-auto text-xs text-text-disabled">{speakers.length}</span>
      </div>

      {/* Speaker list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
        {speakers.length === 0 && (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <div className="h-10 w-10 rounded-xl bg-surface-4 border border-border flex items-center justify-center mb-2">
              <Users size={16} className="text-text-disabled" />
            </div>
            <p className="text-xs text-text-muted">No speakers detected</p>
          </div>
        )}

        {speakers.map((sp, idx) => {
          const color = sp.color ?? getSpeakerColor(idx)
          const isSelected = selectedSpeakerId === sp.id
          const isEditing = editingId === sp.id
          const segmentCount = segments
            ? segments.filter((s) => s.speaker_id === sp.id).length
            : (sp.segment_count ?? 0)

          return (
            <motion.div
              key={sp.id}
              className={cn(
                'rounded-xl border transition-all duration-150 overflow-hidden',
                isSelected
                  ? 'border-brand/40 bg-brand/8'
                  : 'border-border bg-surface-3 hover:border-border-strong hover:bg-surface-4'
              )}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: idx * 0.04 }}
            >
              <div
                className="flex items-center gap-2.5 px-3 py-2.5 cursor-pointer"
                onClick={() => setSelectedSpeaker(isSelected ? null : sp.id)}
              >
                {/* Color dot */}
                <div
                  className="h-8 w-8 rounded-lg flex items-center justify-center shrink-0 text-white font-bold text-sm"
                  style={{ background: color }}
                >
                  {(sp.name ?? sp.label ?? 'S')[0].toUpperCase()}
                </div>

                {/* Name / edit */}
                <div className="flex-1 min-w-0">
                  {isEditing ? (
                    <input
                      className="glass-input rounded-md px-2 py-0.5 text-xs text-text-primary w-full focus:outline-none"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') saveEdit(sp)
                        if (e.key === 'Escape') cancelEdit()
                      }}
                      autoFocus
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <div>
                      <p className="text-xs font-medium text-text-primary truncate">
                        {sp.name ?? sp.label ?? `Speaker ${idx + 1}`}
                      </p>
                      <p className="text-[10px] text-text-disabled">{segmentCount} segment{segmentCount !== 1 ? 's' : ''}</p>
                    </div>
                  )}
                </div>

                {/* Edit actions */}
                <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                  {isEditing ? (
                    <>
                      <button
                        className="h-6 w-6 rounded-md flex items-center justify-center text-emerald-400 hover:bg-emerald-500/15 transition-colors"
                        onClick={() => saveEdit(sp)}
                      >
                        <Check size={12} />
                      </button>
                      <button
                        className="h-6 w-6 rounded-md flex items-center justify-center text-text-muted hover:bg-white/8 transition-colors"
                        onClick={cancelEdit}
                      >
                        <X size={12} />
                      </button>
                    </>
                  ) : (
                    <button
                      className="h-6 w-6 rounded-md flex items-center justify-center text-text-disabled hover:text-text-primary hover:bg-white/8 transition-colors opacity-0 group-hover:opacity-100"
                      onClick={() => startEdit(sp)}
                    >
                      <Edit2 size={11} />
                    </button>
                  )}
                </div>
              </div>

              {/* Color picker (expanded when selected) */}
              {isSelected && !isEditing && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="px-3 pb-2.5"
                >
                  <p className="text-[10px] text-text-disabled mb-1.5">Track color</p>
                  <div className="flex gap-1.5">
                    {PRESET_COLORS.map((c) => (
                      <button
                        key={c}
                        className={cn(
                          'h-5 w-5 rounded-full border-2 transition-transform hover:scale-110',
                          color === c ? 'border-white' : 'border-transparent'
                        )}
                        style={{ background: c }}
                        onClick={(e) => { e.stopPropagation(); handleColorChange(sp, c) }}
                      />
                    ))}
                  </div>
                  <button
                    className="mt-2 text-[10px] text-text-muted hover:text-brand-400 transition-colors"
                    onClick={(e) => { e.stopPropagation(); startEdit(sp) }}
                  >
                    ✎ Edit name
                  </button>
                </motion.div>
              )}
            </motion.div>
          )
        })}
      </div>
    </div>
  )
}
