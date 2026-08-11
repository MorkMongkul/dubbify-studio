// src/components/panels/SpeakersPanel.tsx
// Speakers tab of the dock — rename the session's speakers and assign each a
// library voice. Extracted from EditorPage; fully self-contained (React Query
// dedupes the segment/speaker subscriptions it shares with the editor).
import { useState } from 'react'
import { Users } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useSegments, useSpeakers, useVoices, useUpdateSpeaker } from '@/hooks/useApi'
import { getSpeakerColor, getSpeakerDisplayName } from '@/lib/utils'
import { recordSpeakerChange } from '@/lib/historyHelpers'
import { PanelHeader, PanelEmptyState } from './PanelShell'

interface SpeakersPanelProps {
  projectId?: string
  jobId?: string
}

export function SpeakersPanel({ projectId, jobId }: SpeakersPanelProps) {
  const qc = useQueryClient()
  const { data: segs = [] } = useSegments(jobId ?? null)
  const { data: spks = [] } = useSpeakers(projectId ?? null)
  const { data: availableVoices = [] } = useVoices()
  const updateSpeaker = useUpdateSpeaker()
  const [editingSpeakerId, setEditingSpeakerId] = useState<string | null>(null)
  const [editingSpeakerName, setEditingSpeakerName] = useState('')

  // Speakers actually present in this session, each keeping the index it has
  // in the FULL project speaker list: that index is what getSpeakerColor() is
  // keyed on everywhere else (timeline clips, transcript rows), so colouring
  // by position in a filtered subset would desync the dock from the timeline.
  const sessionSpeakerIds = new Set(segs.map(s => s.speaker_id).filter(Boolean))
  const sessionSpeakers = spks
    .map((sp, projectIndex) => ({ sp, projectIndex }))
    .filter(({ sp }) => sessionSpeakerIds.has(sp.id))
    .sort((a, b) => a.sp.label.localeCompare(b.sp.label))

  if (!jobId || !projectId) {
    return (
      <div className="h-full flex flex-col min-h-0">
        <PanelHeader icon={Users} title="Speakers" />
        <PanelEmptyState
          icon={Users}
          title="No session open"
          hint="Speakers appear here once a session's video has been analyzed."
        />
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader icon={Users} title="Speakers" count={sessionSpeakers.length} />
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {sessionSpeakers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-center">
            <Users size={18} className="text-zinc-700 mb-2" />
            <p className="text-[10px] text-zinc-600">No speakers yet</p>
            <p className="text-[9px] text-zinc-700 mt-0.5">Appear here once segments are analyzed</p>
          </div>
        ) : (
          sessionSpeakers.map(({ sp: speaker, projectIndex }) => {
            const color = speaker.color ?? getSpeakerColor(projectIndex)
            return (
              <div key={speaker.id} className="p-2 rounded bg-zinc-800/30 border border-zinc-800/40 space-y-1.5">
                <div className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: color }} />
                  {editingSpeakerId === speaker.id ? (
                    <input
                      autoFocus
                      className="bg-zinc-950 text-[11px] text-zinc-200 rounded px-1 py-0.5 flex-1 min-w-0 focus:outline-none border border-purple-500/50"
                      value={editingSpeakerName}
                      onChange={(e) => setEditingSpeakerName(e.target.value)}
                      onBlur={() => {
                        const trimmed = editingSpeakerName.trim()
                        if (trimmed && trimmed !== speaker.display_name) {
                          const before = speaker.display_name
                          updateSpeaker.mutate({ speakerId: speaker.id, data: { display_name: trimmed } })
                          recordSpeakerChange(qc, projectId, speaker.id,
                            { display_name: before }, { display_name: trimmed }, 'Rename speaker')
                        }
                        setEditingSpeakerId(null)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') e.currentTarget.blur()
                        if (e.key === 'Escape') setEditingSpeakerId(null)
                      }}
                    />
                  ) : (
                    <span
                      className="text-[11px] font-medium text-zinc-200 truncate flex-1 cursor-text hover:text-white transition-colors"
                      onClick={() => {
                        setEditingSpeakerId(speaker.id)
                        setEditingSpeakerName(speaker.display_name || getSpeakerDisplayName(speaker, projectIndex))
                      }}
                      title="Click to rename"
                    >
                      {getSpeakerDisplayName(speaker, projectIndex)}
                    </span>
                  )}
                </div>
                <select
                  className="w-full bg-zinc-900 text-zinc-300 text-[10px] rounded border border-zinc-700/60 hover:border-zinc-600 py-1 px-1.5 focus:outline-none focus:border-purple-500/50 cursor-pointer"
                  value={speaker.voice_id || ''}
                  onChange={(e) => {
                    const before = speaker.voice_id ?? null
                    const after = e.target.value || null
                    updateSpeaker.mutate({ speakerId: speaker.id, data: { voice_id: after } })
                    recordSpeakerChange(qc, projectId, speaker.id,
                      { voice_id: before }, { voice_id: after }, 'Change speaker voice')
                  }}
                  title="Voice used for all of this speaker's clips"
                >
                  <option value="">Auto (voice design)</option>
                  {availableVoices.map((v) => (
                    <option key={v.id} value={v.id}>{v.name}</option>
                  ))}
                </select>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
