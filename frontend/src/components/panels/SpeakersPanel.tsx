// src/components/panels/SpeakersPanel.tsx
// Speakers tab of the dock — rename the session's speakers and assign each a
// library voice. Extracted from EditorPage; fully self-contained (React Query
// dedupes the segment/speaker subscriptions it shares with the editor).
import { VoiceBrowser } from '@/components/voices/VoiceBrowser'
import { VoiceTrigger } from '@/components/voices/VoiceTrigger'
import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AudioLines, ChevronLeft, RefreshCw, Users } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  useAnalyzeJob, useDetectSpeakers, useJob, useSegments, useSpeakers, useVoices, useUpdateSpeaker,
} from '@/hooks/useApi'
import { getSpeakerColor, getSpeakerDisplayName, isJobRunning } from '@/lib/utils'
import { recordSpeakerChange } from '@/lib/historyHelpers'
import { voiceUsers } from '@/lib/voiceTags'
import type { Speaker } from '@/types'
import { useHistoryStore } from '@/store/historyStore'
import { PanelHeader, PanelEmptyState } from './PanelShell'

interface SpeakersPanelProps {
  projectId?: string
  jobId?: string
}

export function SpeakersPanel({ projectId, jobId }: SpeakersPanelProps) {
  const qc = useQueryClient()
  const { data: segs = [] } = useSegments(jobId ?? null)
  const { data: spks = [] } = useSpeakers(projectId ?? null)
  const { data: availableVoices = [], isSuccess: voicesLoaded } = useVoices()
  const updateSpeaker = useUpdateSpeaker()
  const [editingSpeakerId, setEditingSpeakerId] = useState<string | null>(null)
  const [editingSpeakerName, setEditingSpeakerName] = useState('')
  const { data: job } = useJob(jobId ?? null)
  const detectSpeakers = useDetectSpeakers()
  const analyze = useAnalyzeJob()
  const [confirmReanalyze, setConfirmReanalyze] = useState(false)
  // The speaker whose voice is being chosen: the voice browser covers the list meanwhile
  const [browsingId, setBrowsingId] = useState<string | null>(null)

  // Speakers actually present in this session, each keeping the index it has
  // in the FULL project speaker list: that index is what getSpeakerColor() is
  // keyed on everywhere else (timeline clips, transcript rows), so colouring
  // by position in a filtered subset would desync the dock from the timeline.
  const sessionSpeakerIds = new Set(segs.map(s => s.speaker_id).filter(Boolean))
  const sessionSpeakers = spks
    .map((sp, projectIndex) => ({ sp, projectIndex }))
    .filter(({ sp }) => sessionSpeakerIds.has(sp.id))
    .sort((a, b) => a.sp.label.localeCompare(b.sp.label))

  const browsing = sessionSpeakers.find(({ sp }) => sp.id === browsingId)
  // The speaker left the episode meanwhile (e.g. its last line went to someone
  // else): close for good, so the browser doesn't pop up again if it returns
  if (browsingId && !browsing) setBrowsingId(null)

  const chooseVoice = (speaker: Speaker, voiceId: string | null) => {
    setBrowsingId(null)
    const before = speaker.voice_id ?? null
    if (before === voiceId) return
    updateSpeaker.mutate({ speakerId: speaker.id, data: { voice_id: voiceId } })
    recordSpeakerChange(qc, projectId!, speaker.id, { voice_id: before }, { voice_id: voiceId }, 'Change speaker voice')
  }

  const busy = job ? isJobRunning(job.status) : false
  const finished = job?.status === 'completed' || job?.status === 'failed'
  // The actions stay in place while a step runs, just unavailable: what's
  // running shows in the transcript panel, not here as well.
  const showActions = segs.length > 0 && (finished || busy)
  const UNAVAILABLE = 'Available when the current step finishes'

  const runDetectSpeakers = () => {
    if (!jobId) return
    detectSpeakers.mutate(jobId, {
      onSuccess: () => toast.success('Detecting speakers — your text, translations and voices are kept'),
      onError: (e) => toast.error((e as Error).message || 'Could not start speaker detection'),
    })
  }

  const runReanalyze = () => {
    if (!jobId) return
    setConfirmReanalyze(false)
    analyze.mutate({ jobId }, {
      onSuccess: () => {
        // The new segments get new ids — older undo steps would point at nothing.
        useHistoryStore.getState().clear()
        toast.success('Re-analyzing — the new segments replace the current ones when ready')
      },
      onError: (e) => toast.error((e as Error).message || 'Could not start re-analysis'),
    })
  }

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
    <div className="h-full flex flex-col min-h-0 relative">
      <PanelHeader icon={Users} title="Speakers" count={sessionSpeakers.length} />
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {showActions && (
          <div className="p-2 rounded bg-ink-800/30 border border-ink-800/40 space-y-1.5">
            {!busy && sessionSpeakers.length === 1 && segs.length >= 4 && (
              <p className="text-[9px] text-amber-300/80">
                Only one voice is assigned. If more people speak in this video, detect speakers again.
              </p>
            )}
            <button
              onClick={runDetectSpeakers}
              disabled={busy || detectSpeakers.isPending}
              title={busy ? UNAVAILABLE : undefined}
              className="w-full flex items-center justify-center gap-1.5 py-1 rounded bg-brand-600 hover:bg-brand-500 disabled:opacity-40 disabled:hover:bg-brand-600 disabled:cursor-not-allowed text-white text-[10px] font-semibold transition-colors"
            >
              <AudioLines size={11} />
              Detect speakers
            </button>
            <p className="text-[9px] text-ink-500 leading-snug">
              Re-listens to the audio and reassigns who speaks each line. Text, translations and
              generated voices stay as they are.
            </p>
            {confirmReanalyze && !busy ? (
              <div className="space-y-1.5 pt-1 border-t border-ink-800/60">
                <p className="text-[9px] text-amber-300/90 leading-snug">
                  Replace every segment with a fresh transcription, speakers and translation?
                  Your text edits and generated voices in this session will be discarded.
                </p>
                <div className="flex gap-1.5">
                  <button
                    onClick={runReanalyze}
                    className="flex-1 py-1 rounded bg-red-600/80 hover:bg-red-500 text-white text-[10px] font-semibold transition-colors"
                  >
                    Replace segments
                  </button>
                  <button
                    onClick={() => setConfirmReanalyze(false)}
                    className="flex-1 py-1 rounded bg-ink-800 hover:bg-ink-700 text-ink-300 text-[10px] transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setConfirmReanalyze(true)}
                disabled={busy || analyze.isPending}
                className="w-full flex items-center justify-center gap-1.5 py-1 rounded bg-ink-800 hover:bg-ink-700 disabled:opacity-40 disabled:hover:bg-ink-800 disabled:cursor-not-allowed text-ink-300 text-[10px] transition-colors"
                title={busy ? UNAVAILABLE : 'Transcribe, detect speakers and translate this session again from scratch'}
              >
                <RefreshCw size={10} />
                Re-analyze…
              </button>
            )}
          </div>
        )}
        {sessionSpeakers.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-center">
            <Users size={18} className="text-ink-700 mb-2" />
            <p className="text-[10px] text-ink-600">No speakers yet</p>
            <p className="text-[9px] text-ink-700 mt-0.5">They appear here when the episode has been analyzed</p>
          </div>
        ) : (
          sessionSpeakers.map(({ sp: speaker, projectIndex }) => {
            const color = speaker.color ?? getSpeakerColor(projectIndex)
            return (
              <div key={speaker.id} className="p-2 rounded bg-ink-800/30 border border-ink-800/40 space-y-1.5">
                <div className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: color }} />
                  {editingSpeakerId === speaker.id ? (
                    <input
                      autoFocus
                      className="bg-ink-950 text-[11px] text-ink-200 rounded px-1 py-0.5 flex-1 min-w-0 focus:outline-none border border-brand-500/50"
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
                      className="text-[11px] font-medium text-ink-200 truncate flex-1 cursor-text hover:text-white transition-colors"
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
                <VoiceTrigger
                  voice={speaker.voice_id ? availableVoices.find((v) => v.id === speaker.voice_id) : undefined}
                  missing={voicesLoaded && !!speaker.voice_id && !availableVoices.some((v) => v.id === speaker.voice_id)}
                  emptyLabel={speaker.voice_id && !voicesLoaded ? 'Loading…' : 'Choose for me'}
                  emptyHint={speaker.voice_id && !voicesLoaded ? '' : "A voice that fits the actor is picked the first time it's voiced"}
                  playerId={`card:${speaker.id}`}
                  onOpen={() => setBrowsingId(speaker.id)}
                  title="Choose the voice for all of this speaker's lines"
                />
                {speaker.voice_auto && speaker.voice_id && (
                  <p className="text-[9.5px] text-ink-500 leading-snug">
                    Picked for you to fit the actor — change it if it doesn't suit.
                  </p>
                )}
              </div>
            )
          })
        )}
      </div>

      <AnimatePresence>
        {browsing && (
          <motion.div
            key="voice-browser"
            className="absolute inset-0 z-20 flex flex-col bg-ink-900"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 420, damping: 40 }}
          >
            <VoiceBrowser
              className="flex-1"
              voices={availableVoices}
              currentVoiceId={browsing.sp.voice_id ?? null}
              usedBy={voiceUsers(sessionSpeakers.map(({ sp }) => sp), browsing.sp.id)}
              auto={{
                label: 'Choose for me',
                hint: "A built-in voice that fits the actor is picked the first time this speaker is voiced, and kept.",
              }}
              onChoose={(voiceId) => chooseVoice(browsing.sp, voiceId)}
              onClose={() => setBrowsingId(null)}
              header={
                <div className="flex items-center gap-2 px-3 h-11 border-b border-ink-800/60 shrink-0">
                  <button
                    onClick={() => setBrowsingId(null)}
                    className="flex items-center gap-0.5 text-[11px] text-ink-400 hover:text-white transition-colors"
                  >
                    <ChevronLeft size={14} /> Speakers
                  </button>
                  <span className="text-[12px] font-semibold text-white ml-1">Voice for</span>
                  <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: browsing.sp.color ?? getSpeakerColor(browsing.projectIndex) }} />
                  <span className="text-[12px] font-semibold text-white truncate">
                    {getSpeakerDisplayName(browsing.sp, browsing.projectIndex)}
                  </span>
                </div>
              }
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
