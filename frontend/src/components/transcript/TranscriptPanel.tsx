// src/components/transcript/TranscriptPanel.tsx
import { VoiceBrowser } from '@/components/voices/VoiceBrowser'
import { VoiceTrigger } from '@/components/voices/VoiceTrigger'
import { useRef, useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { AlignLeft, Loader2, ChevronLeft, ChevronRight, Sliders, Volume2, Type, RefreshCw, Mic, Plus, Languages, AudioLines, X } from 'lucide-react'
import type { Segment, SegmentUpdate, Speaker, Job } from '@/types'
import { useEditorStore, useActiveSegmentId } from '@/store/editorStore'
import { SegmentCardSkeleton } from '@/components/ui/Skeleton'
import { Modal, InputField, Field } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { toast } from 'sonner'
import { cn, formatTime, getSpeakerColor } from '@/lib/utils'
import { useQueryClient } from '@tanstack/react-query'
import { segments as segmentsApi, tts as ttsApi, type BatchSynthesisStatus } from '@/api/client'
import { useAnalyzeJob, useJob, useVoices, useSynthesizeSegment, useSynthesizeBatch, useCreateSegment, useCreateSpeaker, useTranslateMissing, useRetrySeparation, putSegments } from '@/hooks/useApi'
import { JobProgressRing } from '@/components/progress/JobProgressRing'
import { useDoneFlash } from '@/hooks/useDoneFlash'
import { jobProgress } from '@/lib/jobProgress'
import { recordSegmentChange, recordSegmentsChange, recordSegmentCreate } from '@/lib/historyHelpers'
import { voiceUsers } from '@/lib/voiceTags'
import { effectiveSpeed } from '@/lib/clipRules'

interface TranscriptPanelProps {
  segments: Segment[]
  speakers: Speaker[]
  jobId: string
  projectId: string
  isLoading?: boolean
  className?: string
  job?: Job
}

export function TranscriptPanel({
  segments, speakers, jobId, projectId, isLoading, className, job
}: TranscriptPanelProps) {
  const activeSegmentId = useActiveSegmentId()
  // Individual action selectors (zustand actions are stable references) — a
  // whole-store destructure would re-render this panel on every playhead tick.
  const setCurrentTime          = useEditorStore((s) => s.setCurrentTime)
  const setRightPanelCollapsed  = useEditorStore((s) => s.setRightPanelCollapsed)
  const toggleSelectSegment     = useEditorStore((s) => s.toggleSelectSegment)
  const toggleSelectAllSegments = useEditorStore((s) => s.toggleSelectAllSegments)
  const setInspectorMode        = useEditorStore((s) => s.setInspectorMode)
  const setFocusedTimelineItemId = useEditorStore((s) => s.setFocusedTimelineItemId)
  const setActiveSegment        = useEditorStore((s) => s.setActiveSegment)
  const setAssistantLineId      = useEditorStore((s) => s.setAssistantLineId)
  const updateSegmentText       = useEditorStore((s) => s.updateSegmentText)
  const clearSegmentTextOverride = useEditorStore((s) => s.clearSegmentTextOverride)
 
  const selectedSegmentIds = useEditorStore((s) => s.selectedSegmentIds)
  const pendingSelectedCount = selectedSegmentIds.filter((id) => {
    const seg = segments.find((s) => s.id === id)
    return seg && !seg.tts_audio_path
  }).length
  const { data: availableVoices = [] } = useVoices()
  // Lines with words but no translation — e.g. after a translation outage.
  // They stay flagged (not just a toast) until translated, with a one-click retry.
  const translateMissing = useTranslateMissing()
  // "Audio ready" offers Analyze right there (the Vocals track has it too)
  const analyze = useAnalyzeJob()
  const missingTranslations = useMemo(
    () => segments.filter((s) => s.source_text?.trim() && !s.khmer_text?.trim()).length,
    [segments],
  )
  const jobIdle = !job || job.status === 'completed' || job.status === 'failed'
  // The one place a running step shows its progress: the full view below
  // before the episode has lines, a thin bar above them after (the top bar
  // takes over only while this panel is hidden).
  const jobAction = useEditorStore((s) => s.jobActions[jobId])
  const progress = jobProgress(job?.status, segments.length > 0, jobAction)
  // Before the episode has lines the progress is a ring, which says Done for a
  // moment when the analysis finishes, before the lines take its place
  // (only once this panel has seen the job running for itself, so coming back
  // to an episode that finished meanwhile doesn't flash Done). A finished
  // analysis is Done even with a warning: the lines are there, and the
  // transcript's banners explain any gaps.
  const { isFetchedAfterMount: jobFresh } = useJob(jobId)
  const analysisDone = useDoneFlash(!!progress && segments.length === 0 && jobFresh, job?.status === 'completed')

  // Music/voices separation fell back to the original audio: say so for as
  // long as it's true, and offer to try again (lines and voices are kept).
  const retrySeparation = useRetrySeparation()
  // Also before Analyze: transcription listens to the separated voices
  const separationFailed = job?.stems_separated === false && (jobIdle || job.status === 'stems_ready')
  const handleRetrySeparation = () => {
    retrySeparation.mutate(jobId, {
      onError: (err) => toast.error(`Couldn't start: ${(err as Error).message}`),
    })
  }
  const prevStatusRef = useRef(job?.status)
  useEffect(() => {
    const prev = prevStatusRef.current
    prevStatusRef.current = job?.status
    if (prev !== 'separating' || job?.status !== 'completed') return
    if (job.stems_separated) toast.success('Music and voices are separated now')
    else toast.error(job.error_msg || "Music and voices still couldn't be separated.", { duration: 12_000 })
  }, [job?.status, job?.stems_separated, job?.error_msg])
  const handleTranslateMissing = () => {
    translateMissing.mutate(jobId, {
      onSuccess: (r) => toast.success(`Translating ${r.lines} line${r.lines === 1 ? '' : 's'}…`),
      onError: (e) => toast.error((e as Error).message || 'Could not start the translation'),
    })
  }
  const inspectorMode = useEditorStore((s) => s.inspectorMode)
  const focusedTimelineItemId = useEditorStore((s) => s.focusedTimelineItemId)
 
  const qc = useQueryClient()
  const [singleSynthesizing, setSingleSynthesizing] = useState<string | null>(null)
  const batchGeneratingIds = useEditorStore((s) => s.batchGeneratingIds)
  const batchGeneratingJobId = useEditorStore((s) => s.batchGeneratingJobId)
  // A batch may belong to a different episode than the one on screen — its
  // badges/banner/progress only render when viewing that episode, but the
  // polling effect below tracks it either way.
  const anyBatchRunning = batchGeneratingIds.length > 0
  const batchSynthesizing = anyBatchRunning && batchGeneratingJobId === jobId
  // The backend's own count — "has audio" can't tell a regenerated line from an old one
  const [batchProgress, setBatchProgress] = useState<BatchSynthesisStatus | null>(null)
  const batchDoneCount = batchSynthesizing ? (batchProgress?.done ?? 0) : 0
  const synthesizeSegment = useSynthesizeSegment()
  const synthesizeBatch = useSynthesizeBatch()
  const createSegment = useCreateSegment()
  const createSpeaker = useCreateSpeaker()
  const [newSpeakerModal, setNewSpeakerModal] = useState<{ segmentId: string } | null>(null)
  const [newSpeakerName, setNewSpeakerName] = useState('')
  const [newSpeakerVoice, setNewSpeakerVoice] = useState('')   // '' = choose for me
  // The New Speaker box shows the voice browser instead of its form
  const [pickingNewSpeakerVoice, setPickingNewSpeakerVoice] = useState(false)
  // The line whose own voice is being chosen (the voice browser in a window)
  const [lineVoiceFor, setLineVoiceFor] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const cardRefs = useRef<Map<string, HTMLTableRowElement>>(new Map())
  const originalTextRef = useRef<string>('')

 
  // Inspector settings states
  const [activeClipTab, setActiveClipTab] = useState<'basic' | 'filter' | 'speed'>('basic')
  const [clipVolume, setClipVolume] = useState<number>(0) // dB
  const [clipFilter, setClipFilter] = useState<string | null>(null)
  const [clipSpeed, setClipSpeed] = useState<number>(1.0) // x

  const [fontSize, setFontSize] = useState<number>(14)
  const [textAlignment, setTextAlignment] = useState<'left' | 'center' | 'right'>('center')

  useEffect(() => {
    const id = focusedTimelineItemId || activeSegmentId
    if (!id) return
    const el = cardRefs.current.get(id)
    if (!el || !scrollRef.current) return
    const { top, bottom } = el.getBoundingClientRect()
    const { top: cTop, bottom: cBottom } = scrollRef.current.getBoundingClientRect()
    if (top < cTop + 40 || bottom > cBottom - 40) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [activeSegmentId, focusedTimelineItemId])


  const getSpeaker = (seg: Segment) => speakers.find((sp) => sp.id === seg.speaker_id)
  const getSpeakerIndex = (seg: Segment) => speakers.findIndex((sp) => sp.id === seg.speaker_id)

  // `speakers` is every speaker in the whole PROJECT (workspace-level fetch —
  // see useSpeakers(projectId) in EditorPage), not just this episode. That's
  // fine for color/index lookups above, but the per-row reassignment dropdown
  // below must only offer speakers who actually appear in THIS job — assigning
  // a line to a speaker who only exists in some other episode is meaningless.
  // With one episode per project this was invisible (the two lists matched);
  // once a project holds many episodes (e.g. after consolidating episodes
  // into one project) the unfiltered list balloons to every speaker across
  // every episode, mostly duplicate "SPEAKER_00"/"SPEAKER_01" entries that
  // were never renamed. Mirrors the identical filter in SpeakersPanel.
  const jobSpeakers = useMemo(() => {
    const idsInThisJob = new Set(segments.map((s) => s.speaker_id).filter(Boolean))
    return speakers.filter((sp) => idsInThisJob.has(sp.id))
  }, [segments, speakers])

  const handleResetInspector = () => {
    setInspectorMode('global_synthesis')
    setFocusedTimelineItemId(null)
  }
 
  const handleVoiceChange = async (segmentId: string, voiceId: string) => {
    const seg = segments.find((s) => s.id === segmentId)
    try {
      await segmentsApi.update(segmentId, { voice_id: voiceId || null })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      if (seg) {
        recordSegmentChange(qc, jobId, segmentId,
          { voice_id: seg.voice_id ?? null }, { voice_id: voiceId || null }, 'Change voice')
      }
    } catch (err) {
      toast.error('Failed to assign voice')
      console.error(err)
    }
  }

  // Manually add a new subtitle segment — used for the "build the transcript by
  // hand" workflow instead of relying on the automatic diarize/transcribe pipeline.
  // Placed right after the last existing segment; the user then edits its text,
  // assigns a speaker, drags it into place on the timeline, and generates voice.
  const handleAddSegment = async () => {
    const lastEnd = segments.length > 0 ? Math.max(...segments.map((s) => s.end_time)) : 0
    const defaultDur = 3
    const cap = job?.duration_secs && job.duration_secs > 0 ? job.duration_secs : null
    const start = cap && lastEnd >= cap ? Math.max(0, cap - defaultDur) : lastEnd
    const end = cap ? Math.min(start + defaultDur, cap) : start + defaultDur
    try {
      const created = await createSegment.mutateAsync({
        jobId,
        data: { start_time: start, end_time: end, speaker_id: null, lane_index: 0, khmer_text: '' },
      })
      setActiveSegment(created.id)
      setFocusedTimelineItemId(created.id)
      setCurrentTime(created.start_time)
      recordSegmentCreate(qc, jobId, created, 'Add line')
    } catch (err) {
      toast.error('Failed to add a line')
      console.error(err)
    }
  }

  const handleSpeakerSelectChange = async (segmentId: string, value: string) => {
    if (value === '__new__') {
      setNewSpeakerName('')
      setNewSpeakerVoice('')
      setPickingNewSpeakerVoice(false)
      setNewSpeakerModal({ segmentId })
      return
    }
    const seg = segments.find((s) => s.id === segmentId)
    try {
      await segmentsApi.update(segmentId, { speaker_id: value || null })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      if (seg) {
        recordSegmentChange(qc, jobId, segmentId,
          { speaker_id: seg.speaker_id ?? null }, { speaker_id: value || null }, 'Reassign speaker')
      }
    } catch (err) {
      toast.error('Failed to assign speaker')
      console.error(err)
    }
  }

  const handleCreateSpeakerConfirm = async () => {
    if (!newSpeakerModal) return
    const name = newSpeakerName.trim()
    if (!name) {
      toast.error('Name is required')
      return
    }
    try {
      const seg = segments.find((s) => s.id === newSpeakerModal.segmentId)
      const speaker = await createSpeaker.mutateAsync({
        projectId,
        // No voice chosen: the backend picks a fitting built-in one the first
        // time this speaker is voiced (never a random voice per line).
        data: { display_name: name, voice_id: newSpeakerVoice || null },
      })
      await segmentsApi.update(newSpeakerModal.segmentId, { speaker_id: speaker.id })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      // Only the segment's speaker_id is undoable — there's no delete-speaker
      // endpoint, so the newly-created Speaker itself stays even after undo.
      if (seg) {
        recordSegmentChange(qc, jobId, newSpeakerModal.segmentId,
          { speaker_id: seg.speaker_id ?? null }, { speaker_id: speaker.id }, 'Assign new speaker')
      }
      setNewSpeakerModal(null)
    } catch (err) {
      toast.error('Failed to create speaker')
      console.error(err)
    }
  }

  // Persist a text edit to the DB on blur so it survives reload / resume.
  // The voice is kept: a voiced line shows it was made from older text (New
  // voice makes it again); a line with no voice takes its new text's length,
  // and the server may move it to another track if it now runs into the next
  // clip — the track is part of the undo step.
  const handleTextBlur = async (segmentId: string, text: string, original: string) => {
    if (text === original) return   // nothing changed
    const seg = segments.find((s) => s.id === segmentId)
    try {
      const updated = await segmentsApi.update(segmentId, { khmer_text: text })
      // Write the PATCH response straight into the cache, then drop the local
      // text override so server state owns this segment's text again — leaving
      // the override in place made it outrank anything that arrived later,
      // including the reverted text an undo had just written.
      putSegments(qc, jobId, [updated])
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      clearSegmentTextOverride(segmentId)
      recordSegmentChange(qc, jobId, segmentId,
        { khmer_text: original, lane_index: seg?.lane_index ?? 0 },
        { khmer_text: text, lane_index: updated.lane_index ?? 0 },
        'Edit text')
    } catch (err) {
      toast.error('Failed to save edit')
      console.error(err)
    }
  }

  const handleSingleSynthesize = async (segmentId: string) => {
    setSingleSynthesizing(segmentId)
    try {
      const res = await synthesizeSegment.mutateAsync({ segmentId, jobId })
      if (res.backup_voice) {
        toast.warning("Voice generated with the backup voice (Gemini) because the voice server didn't respond. Regenerate it when the server is back.")
      } else {
        toast.success('Voice generated')
      }
    } catch (err) {
      toast.error(`Couldn't generate the voice: ${(err as Error).message}`)
      console.error(err)
    } finally {
      setSingleSynthesizing(null)
    }
  }

  // Batch runs as a backend background task (202). Tracking/polling is
  // handled by the batchGeneratingIds effect below — storing the ids is what
  // kicks it off, and clearing the selection right away lets the per-row
  // "Generating…" badges and the progress banner take over. The backend
  // endpoint itself has no "skip if already has audio" logic — it (re)does
  // whatever ids it's given — so passing already-generated ids through here
  // is a real, working force-regenerate, not a no-op.
  const runBatchSynthesize = async (segmentIds: string[], verb: 'Generating' | 'Regenerating', detail = '') => {
    try {
      const res = await synthesizeBatch.mutateAsync({ segmentIds, jobId })
      const ids: string[] = res.segment_ids ?? segmentIds
      setBatchProgress(null)
      useEditorStore.setState({
        batchGeneratingIds: ids, batchGeneratingJobId: jobId, batchId: res.batch_id, selectedSegmentIds: [],
      })
      toast.info(`${verb} ${ids.length} voice${ids.length === 1 ? '' : 's'}…${detail}`)
    } catch (err) {
      toast.error(`Failed to start voice ${verb.toLowerCase()}`)
      console.error(err)
    }
  }

  // Segments without audio yet (never synthesized, or marked stale by a text
  // edit in handleTextBlur) are what "Generate" fills in — already-generated
  // ones in a MIXED selection are left alone, so a big "select all" doesn't
  // waste API calls/time redoing work that's already done. But when EVERY
  // selected segment already has audio, skipping all of them would leave the
  // button doing nothing at all when clicked — "Generate" can then only
  // sensibly mean "regenerate everything I selected", so that's what it does.
  const handleBatchSynthesize = async () => {
    const pendingIds = selectedSegmentIds.filter((id) => {
      const seg = segments.find((s) => s.id === id)
      return seg && !seg.tts_audio_path
    })
    const allAlreadyDone = pendingIds.length === 0

    if (allAlreadyDone) {
      if (selectedSegmentIds.length === 0) return
      if (selectedSegmentIds.length === 1) {
        await handleSingleSynthesize(selectedSegmentIds[0])
        useEditorStore.setState({ selectedSegmentIds: [] })
        return
      }
      await runBatchSynthesize(selectedSegmentIds, 'Regenerating')
      return
    }

    const skipped = selectedSegmentIds.length - pendingIds.length

    if (pendingIds.length === 1 && skipped === 0) {
      await handleSingleSynthesize(pendingIds[0])
      useEditorStore.setState({ selectedSegmentIds: [] })
      return
    }

    await runBatchSynthesize(pendingIds, 'Generating', skipped > 0 ? ` (${skipped} already done, skipped)` : '')
  }

  // Follow a running batch. Effect-driven (keyed on the store, not a
  // handler-local loop) so a panel remount mid-batch — navigation, project
  // switch — resumes tracking automatically. Polls the batch's OWN job
  // (batchGeneratingJobId), which may differ from the episode on screen, and
  // the backend's batch status, which says when every line has succeeded or
  // failed for good — and why.
  useEffect(() => {
    const batchJobId = batchGeneratingJobId
    const batchId = useEditorStore.getState().batchId
    if (batchGeneratingIds.length === 0 || !batchJobId || !batchId) return
    let cancelled = false
    const stopTracking = () =>
      useEditorStore.setState({ batchGeneratingIds: [], batchGeneratingJobId: null, batchId: null })

    ;(async () => {
      while (!cancelled) {
        await new Promise((r) => setTimeout(r, 3000))
        if (cancelled || useEditorStore.getState().batchGeneratingIds.length === 0) return
        let status: BatchSynthesisStatus
        try {
          status = await ttsApi.batchStatus(batchId)
        } catch (err) {
          // 404: the backend restarted and no longer knows this batch.
          if ((err as { response?: { status?: number } }).response?.status === 404) {
            stopTracking()
            toast.warning('Lost track of the voice batch because the app restarted. Lines without a voice show Pending.')
            return
          }
          continue   // busy backend or network blip — try again next pass
        }
        if (cancelled) return
        setBatchProgress(status)
        try {
          qc.setQueryData(['segments', batchJobId], await segmentsApi.listByJob(batchJobId))
        } catch {
          // the next pass refreshes the lines
        }
        qc.invalidateQueries({ queryKey: ['speakers'] })   // speakers without a voice get one picked as lines are voiced
        if (!status.finished) continue

        stopTracking()
        const failed = status.failed.length
        if (failed === 0) {
          toast.success(`Generated ${status.done} voice${status.done === 1 ? '' : 's'}`)
        } else {
          toast.error(
            `${failed} of ${status.total} lines couldn't be voiced: ${status.failed[0].reason}. ` +
            'Select them and generate again.',
            { duration: 12_000 },
          )
        }
        if (status.backup_voice > 0) {
          toast.warning(
            `${status.backup_voice} line${status.backup_voice === 1 ? '' : 's'} used the backup voice (Gemini) ` +
            "because the voice server didn't respond. Regenerate them when the server is back.",
            { duration: 12_000 },
          )
        }
        return
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchGeneratingIds.length > 0, batchGeneratingJobId])

  // Active clip references
  const activeSeg = segments.find(s => s.id === (focusedTimelineItemId || activeSegmentId))
  const activeSpeaker = activeSeg ? getSpeaker(activeSeg) : null
  const activeSpeakerName = activeSpeaker?.display_name || activeSpeaker?.label || (activeSeg ? `Speaker ${getSpeakerIndex(activeSeg) + 1}` : 'Audio Clip')

  // Sync the inspector to the active clip's SAVED effects — on clip change and
  // whenever those values change underneath (undo, another tab), not only on
  // clip change. Adjusted during render rather than in an effect; while a
  // slider is being dragged the saved values don't move, so it isn't fought.
  const savedEffects = activeSeg ? {
    id: activeSeg.id,
    volume: activeSeg.volume_db ?? 0,
    filter: activeSeg.voice_filter ?? null,
    // The speed heard: the voice file's own speed × the clip's (kept from an
    // older episode's fit); a change sets it as the file's speed
    speed: Math.round(effectiveSpeed(activeSeg) * 1000) / 1000,
  } : null
  // Starts empty so the first render with a clip also syncs (the local
  // states above start at neutral defaults).
  const [syncedEffects, setSyncedEffects] = useState<typeof savedEffects>(null)
  if (savedEffects && (
    savedEffects.id !== syncedEffects?.id || savedEffects.volume !== syncedEffects.volume
    || savedEffects.filter !== syncedEffects.filter || savedEffects.speed !== syncedEffects.speed
  )) {
    setSyncedEffects(savedEffects)
    setClipVolume(savedEffects.volume)
    setClipFilter(savedEffects.filter)
    setClipSpeed(savedEffects.speed)
  }

  // A slider saves when released; releasing without a change saves nothing.
  const commitVolume = (value: number) => {
    if (activeSeg && value !== (activeSeg.volume_db ?? 0)) handleVolumeChange(activeSeg.id, value)
  }
  const commitSpeed = (value: number) => {
    if (activeSeg && Math.abs(value - effectiveSpeed(activeSeg)) > 0.0005) handleSpeedChange(activeSeg.id, value)
  }

  // Clip effects target the WHOLE multi-selection when the edited clip is
  // part of it — select-all → set Volume/Filter/Speed must hit every clip,
  // not just the active one. Falls back to the single clip otherwise.
  const effectTargetIds = (segmentId: string): string[] => {
    const sel = useEditorStore.getState().selectedSegmentIds
    return sel.length > 1 && sel.includes(segmentId) ? sel : [segmentId]
  }

  const applyClipEffect = async (
    segmentId: string,
    patch: { volume_db?: number; voice_filter?: string; voice_speed?: number },
    label: string,
    errorMsg: string,
  ) => {
    const targets = effectTargetIds(segmentId)
    const field = Object.keys(patch)[0] as keyof typeof patch
    const changes = targets
      .map((id) => {
        const seg = segments.find((s) => s.id === id)
        if (!seg) return null
        const beforeValue = field === 'volume_db' ? (seg.volume_db ?? 0)
          : field === 'voice_filter' ? (seg.voice_filter ?? '')
          : Math.round(effectiveSpeed(seg) * 1000) / 1000
        // A speed change changes the clip's length: the server may move it to
        // another track, and undo puts it back on its own
        const before: SegmentUpdate = { [field]: beforeValue }
        if (field === 'voice_speed') before.lane_index = seg.lane_index ?? 0
        return { id, before, after: { ...patch } as SegmentUpdate }
      })
      .filter((c): c is NonNullable<typeof c> => !!c && c.before[field] !== patch[field])
    try {
      const updated = await Promise.all(targets.map((id) => segmentsApi.update(id, patch)))
      if (field === 'voice_speed') {
        for (const c of changes) c.after.lane_index = updated.find((u) => u.id === c.id)?.lane_index ?? 0
      }
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      if (changes.length === 1) {
        recordSegmentChange(qc, jobId, changes[0].id, changes[0].before, changes[0].after, label)
      } else if (changes.length > 1) {
        recordSegmentsChange(qc, jobId, changes, `${label} (${changes.length} clips)`)
      }
      if (targets.length > 1) toast.success(`${label} applied to ${targets.length} clips`)
    } catch (err) {
      toast.error(errorMsg)
      console.error(err)
    }
  }

  const handleVolumeChange = (segmentId: string, val: number) =>
    applyClipEffect(segmentId, { volume_db: val }, 'Change volume', 'Failed to update volume')

  const handleFilterChange = (segmentId: string, val: string | null) =>
    applyClipEffect(segmentId, { voice_filter: val || '' }, 'Change voice filter', 'Failed to update voice filter')

  const handleSpeedChange = (segmentId: string, val: number) =>
    applyClipEffect(segmentId, { voice_speed: val }, 'Change voice speed', 'Failed to update voice speed')

  return (
    <div
      className={cn('flex flex-col min-w-0 h-full bg-transparent', className)}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between gap-2 px-3 h-9 border-b border-ink-800/50 shrink-0 bg-ink-900"
      >
        <div className="flex items-center gap-1.5 min-w-0">
          {inspectorMode === 'global_synthesis' ? (
            <>
              <AlignLeft size={12} className="text-brand-400/90 animate-pulse" />
              <span className="text-[11px] font-bold text-brand-400/90 uppercase tracking-wider">
                Transcript Inspector
              </span>
            </>
          ) : (
            <>
              <button
                onClick={handleResetInspector}
                className="text-[10px] text-ink-400 hover:text-white mr-1 flex items-center gap-0.5"
                title="Back to List"
              >
                <ChevronRight size={12} className="rotate-180" />
                <span>Back</span>
              </button>
              <div className="w-px h-3 bg-ink-800/80 mx-1" />
              {inspectorMode === 'audio_clip_settings' ? (
                <Volume2 size={12} className="text-brand-400" />
              ) : (
                <Type size={12} className="text-brand-400" />
              )}
              <span className="text-[11px] font-bold text-ink-200 uppercase tracking-wider truncate">
                {inspectorMode === 'audio_clip_settings' ? activeSpeakerName : 'Subtitle Style'}
              </span>
            </>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {inspectorMode !== 'global_synthesis' ? (
            <button
              onClick={handleResetInspector}
              className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white transition-colors"
              title="Close Settings"
            >
              <ChevronRight size={14} className="rotate-90" />
            </button>
          ) : null}

          {inspectorMode === 'global_synthesis' && (
            <button
              onClick={handleAddSegment}
              disabled={createSegment.isPending || !jobId}
              className="flex items-center gap-1 text-[10px] font-semibold text-brand-400 hover:text-brand-300 hover:bg-brand-500/10 px-1.5 py-0.5 rounded transition-colors disabled:opacity-50"
              title="Add a line"
            >
              <Plus size={12} />
              <span>Add line</span>
            </button>
          )}

          {inspectorMode === 'global_synthesis' && (
            <button
              onClick={() => setRightPanelCollapsed(true)}
              className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white transition-colors"
              title="Collapse transcript"
            >
              <ChevronRight size={14} />
            </button>
          )}
        </div>
      </div>

      {/* A step running on an episode whose lines stay on screen meanwhile:
          speaker detection, re-analysis, missing translations, separation */}
      {progress && segments.length > 0 && (
        <div className="px-3 py-1.5 border-b border-ink-800/50 shrink-0 bg-brand-950/20 flex items-center gap-2">
          <Loader2 size={11} className="animate-spin text-brand-400 shrink-0" />
          <p className="min-w-0 text-[10px] font-semibold text-brand-300 truncate">{progress.title}</p>
        </div>
      )}

      {/* Batch generation progress — independent of selection, so it stays
          visible for the whole run even after the selection is cleared */}
      {batchSynthesizing && inspectorMode === 'global_synthesis' && (
        <div className="px-3 py-1.5 border-b border-ink-800/50 shrink-0 bg-brand-950/20 flex items-center gap-2">
          <Loader2 size={11} className="animate-spin text-brand-400 shrink-0" />
          <span className="text-[10px] font-semibold text-brand-300 whitespace-nowrap">
            Generating voices… {batchDoneCount}/{batchGeneratingIds.length}
          </span>
          <div className="flex-1 h-1 rounded bg-ink-800 overflow-hidden">
            <div
              className="h-full bg-brand-500 transition-all duration-500"
              style={{ width: `${(batchDoneCount / Math.max(1, batchGeneratingIds.length)) * 100}%` }}
            />
          </div>
        </div>
      )}

      {inspectorMode === 'global_synthesis' && separationFailed && (
        <div className="px-3 py-1.5 border-b border-ink-800/50 shrink-0 bg-amber-950/20 flex items-center gap-2">
          <AudioLines size={11} className="text-amber-300 shrink-0" />
          <span className="flex-1 text-[10px] text-amber-200 leading-snug">
            Music and voices weren't separated
          </span>
          <button
            onClick={handleRetrySeparation}
            disabled={retrySeparation.isPending}
            className="shrink-0 text-[10px] font-semibold text-amber-100 bg-amber-600/30 hover:bg-amber-600/50 px-2 py-0.5 rounded transition-colors disabled:opacity-50"
            title="Run the separation again. Lines, translations and voices are kept."
          >
            Try again
          </button>
        </div>
      )}

      {inspectorMode === 'global_synthesis' && missingTranslations > 0 && jobIdle && (
        <div className="px-3 py-1.5 border-b border-ink-800/50 shrink-0 bg-amber-950/20 flex items-center gap-2">
          <Languages size={11} className="text-amber-300 shrink-0" />
          <span className="flex-1 text-[10px] text-amber-200 leading-snug">
            {missingTranslations} line{missingTranslations === 1 ? ' has' : 's have'} no translation
          </span>
          <button
            onClick={handleTranslateMissing}
            disabled={translateMissing.isPending}
            className="shrink-0 text-[10px] font-semibold text-amber-100 bg-amber-600/30 hover:bg-amber-600/50 px-2 py-0.5 rounded transition-colors disabled:opacity-50"
            title="Translate only the lines without a translation — translated and edited lines stay as they are"
          >
            Translate missing lines
          </button>
        </div>
      )}

      {/* Selection Actions Toolbar */}
      <AnimatePresence>
        {selectedSegmentIds.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="px-3 py-2 border-b border-ink-800/50 shrink-0 bg-brand-950/10 text-white flex items-center justify-between gap-2 overflow-hidden"
          >
            <div className="flex items-center gap-1.5 text-[10px] text-brand-600 dark:text-brand-300 font-bold">
              <span>{selectedSegmentIds.length} Selected</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleBatchSynthesize}
                disabled={anyBatchRunning}
                title={anyBatchRunning && !batchSynthesizing
                  ? 'Another episode is still generating voices — wait for it to finish'
                  : pendingSelectedCount === 0
                  ? 'Every selected line already has a voice — this makes them again'
                  : pendingSelectedCount !== selectedSegmentIds.length
                  ? 'Only lines without a voice yet are voiced — the others stay as they are'
                  : undefined}
                className="text-[10px] bg-brand-600 hover:bg-brand-500 text-white px-2.5 py-1 rounded font-semibold transition-all flex items-center gap-1 disabled:opacity-50 cursor-pointer"
              >
                {batchSynthesizing ? <Loader2 size={10} className="animate-spin" /> : <Mic size={10} />}
                <span>
                  {batchSynthesizing
                    ? `Generating ${batchDoneCount}/${batchGeneratingIds.length}…`
                    : pendingSelectedCount === 0
                    ? `Regenerate ${selectedSegmentIds.length} Selected`
                    : <>Generate Voice{pendingSelectedCount !== selectedSegmentIds.length && ` (${pendingSelectedCount} pending)`}</>}
                </span>
              </button>
              <button
                onClick={() => useEditorStore.setState({ selectedSegmentIds: [] })}
                className="text-[10px] text-ink-500 hover:text-ink-300 dark:text-ink-400 dark:hover:text-white px-1.5 py-1 cursor-pointer"
              >
                Clear
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
 
      {/* Main content viewport */}
      <div ref={scrollRef} className="flex-1 overflow-auto bg-transparent">
        {analysisDone ? (
          <div className="flex items-center justify-center h-full p-8">
            <JobProgressRing jobId={jobId} done />
          </div>
        ) : isLoading ? (
          <div className="p-3 space-y-2">
            {[...Array(7)].map((_, i) => <SegmentCardSkeleton key={i} />)}
          </div>
        ) : segments.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center p-8 gap-3">
            {progress ? (
              <JobProgressRing jobId={jobId} />
            ) : job?.status === 'stems_ready' ? (
              /* Stage 1 done — waiting for user to click Analyze */
              <>
                <div className="h-10 w-10 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mb-1">
                  <RefreshCw size={18} className="text-emerald-400" />
                </div>
                <p className="text-[12px] font-semibold text-emerald-400">Audio ready</p>
                <button
                  onClick={() => analyze.mutate({ jobId }, {
                    onError: (e) => toast.error((e as Error).message || 'Could not start the analysis'),
                  })}
                  disabled={analyze.isPending}
                  title="Transcribe, find the speakers and translate"
                  className="mt-1 inline-flex items-center gap-1.5 h-8 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-60 text-white text-[11.5px] font-semibold transition-colors"
                >
                  <AudioLines size={13} />
                  Analyze
                </button>
              </>
            ) : (
              <>
                <AlignLeft size={22} className="text-white/15 mb-1" />
                <p className="text-[12px] text-white/35 font-medium">No lines yet</p>
              </>
            )}
          </div>
        ) : inspectorMode === 'global_synthesis' ? (
          <table className="w-full text-left border-collapse text-[11px]">
            <thead>
              <tr className="border-b border-ink-800 bg-ink-900/50 text-ink-400 font-semibold select-none sticky top-0 z-10">
                <th className="p-2 w-8 text-center">
                  <input
                    type="checkbox"
                    className="rounded border-ink-700 bg-ink-800 text-brand-500 focus:ring-brand-500 focus:ring-offset-ink-900 cursor-pointer h-3 w-3"
                    checked={segments.length > 0 && segments.every((s) => selectedSegmentIds.includes(s.id))}
                    onChange={() => toggleSelectAllSegments(segments.map((s) => s.id))}
                  />
                </th>
                <th className="p-2 w-20">Speakers</th>
                <th className="p-2 w-20">Timestamps</th>
                <th className="p-2">Translation and original</th>
                <th className="p-2 w-28">Voices</th>
                <th className="p-2 w-16 text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800/40">
              {segments.map((seg) => {
                const speaker = getSpeaker(seg)
                const spkIdx = getSpeakerIndex(seg)
                const color = speaker?.color ?? getSpeakerColor(Math.max(0, spkIdx))
                const isSelected = selectedSegmentIds.includes(seg.id)
                const isActive = seg.id === activeSegmentId
                const isDone = !!seg.tts_audio_path
                const isBatchGenerating = batchSynthesizing && !isDone && batchGeneratingIds.includes(seg.id)
                const speakerVoiceName = speaker?.voice_id
                  ? availableVoices.find((v) => v.id === speaker.voice_id)?.name
                  : null

                return (
                  <tr
                    key={seg.id}
                    ref={(el) => {
                      if (el) cardRefs.current.set(seg.id, el)
                      else cardRefs.current.delete(seg.id)
                    }}
                    className={cn(
                      "group hover:bg-ink-900/50 transition-colors cursor-pointer",
                      isActive && "bg-brand-500/15 dark:bg-brand-500/20 hover:bg-brand-500/20 dark:hover:bg-brand-500/25",
                      isSelected && "bg-ink-900"
                    )}
                    onClick={() => {
                      // Choosing a line: the editing keys act on lines again, not a music piece
                      useEditorStore.getState().setSelectedPiece(null)
                      setActiveSegment(seg.id)
                      setCurrentTime(seg.start_time)
                    }}
                    onMouseEnter={() => {
                      setActiveSegment(seg.id)
                      setFocusedTimelineItemId(seg.id)
                    }}
                    onMouseLeave={() => {
                      setFocusedTimelineItemId(null)
                    }}
                  >
                    {/* Checkbox */}
                    <td className="p-2 text-center" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        className="rounded border-ink-700 bg-ink-800 text-brand-500 focus:ring-brand-500 focus:ring-offset-ink-900 cursor-pointer h-3 w-3"
                        checked={isSelected}
                        onChange={() => toggleSelectSegment(seg.id)}
                      />
                    </td>
 
                    {/* Speaker */}
                    <td className="p-2 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center gap-1.5">
                        <span className="h-1.5 w-1.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
                        <select
                          className="bg-ink-900 text-ink-200 text-[10px] rounded border border-ink-800 hover:border-ink-700 hover:bg-ink-800 py-0.5 px-1 focus:outline-none focus:border-brand-500/50 cursor-pointer w-full max-w-[90px] truncate"
                          value={seg.speaker_id ?? ''}
                          onChange={(e) => handleSpeakerSelectChange(seg.id, e.target.value)}
                        >
                          <option value="">Unassigned</option>
                          {jobSpeakers.map((sp) => (
                            <option key={sp.id} value={sp.id}>
                              {sp.display_name || sp.label}
                            </option>
                          ))}
                          <option value="__new__">+ New Speaker…</option>
                        </select>
                      </div>
                    </td>
 
                    {/* Time */}
                    <td className={cn(
                      "p-2 whitespace-nowrap font-mono text-[10px] transition-colors",
                      isActive ? "text-brand-600 dark:text-brand-400 font-bold" : "text-ink-400"
                    )}>
                      {formatTime(seg.start_time)} - {formatTime(seg.end_time)}
                    </td>
 
                    {/* Target Translation */}
                    <td className="p-2 whitespace-normal break-words text-ink-200">
                      <textarea
                        value={seg.khmer_text || ''}
                        onChange={(e) => updateSegmentText(seg.id, e.target.value)}
                        onFocus={(e) => {
                          originalTextRef.current = e.target.value
                          setAssistantLineId(seg.id)   // "this line" for the Assistant
                        }}
                        onBlur={(e) => handleTextBlur(seg.id, e.target.value, originalTextRef.current)}
                        onClick={(e) => {
                          e.stopPropagation()
                          setActiveSegment(seg.id)
                        }}
                        className={cn(
                          "w-full bg-transparent resize-none focus:outline-none focus:ring-1 focus:ring-brand-500/50 rounded-sm py-1 px-1.5 whitespace-normal break-words transition-colors",
                          isActive
                            ? "text-white font-semibold"
                            : "text-ink-100"
                        )}
                        rows={2}
                      />
                      {seg.source_text && (
                        // The original line, for checking the translation against it
                        <p lang="zh-Hans" className="px-1.5 pb-0.5 text-[11px] leading-snug text-ink-500 select-text">
                          {seg.source_text}
                        </p>
                      )}
                    </td>

                    {/* Voice Clone Select */}
                    <td className="p-2" onClick={(e) => e.stopPropagation()}>
                      {(() => {
                        const ownVoiceName = seg.voice_id ? availableVoices.find((v) => v.id === seg.voice_id)?.name : null
                        return (
                          <button
                            onClick={() => setLineVoiceFor(seg.id)}
                            className={cn(
                              'flex items-center gap-1 bg-ink-900 text-[10px] rounded border border-ink-800 hover:border-ink-700 hover:bg-ink-800 py-0.5 px-1.5 focus:outline-none focus:border-brand-500/50 w-full max-w-[110px] text-left',
                              ownVoiceName ? 'text-ink-200' : 'text-ink-500',
                            )}
                            title={ownVoiceName ? `This line uses ${ownVoiceName}`
                              : speakerVoiceName ? `Uses the speaker's voice, ${speakerVoiceName}`
                              : 'No voice chosen yet — a fitting built-in voice is picked the first time this line is voiced'}
                          >
                            <span className="truncate flex-1">{ownVoiceName || speakerVoiceName || 'Choose for me'}</span>
                            <ChevronRight size={10} className="shrink-0 opacity-60" />
                          </button>
                        )
                      })()}
                    </td>

                    {/* Status Badge */}
                    <td className="p-2 text-center whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-center min-h-[22px] relative">
                        {isBatchGenerating ? (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-brand-500/10 text-brand-300 border border-brand-500/20">
                            <Loader2 size={8} className="animate-spin" />
                            Generating
                          </span>
                        ) : isDone ? (
                          <>
                            <span className={cn(
                              "inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 transition-all",
                              (isActive || singleSynthesizing === seg.id) ? "hidden" : "group-hover:hidden"
                            )}>
                              Done
                            </span>
                            <button
                              onClick={() => handleSingleSynthesize(seg.id)}
                              disabled={singleSynthesizing !== null}
                              className={cn(
                                "hidden items-center gap-0.5 px-2 py-0.5 rounded-full text-[9px] font-bold bg-brand-600 hover:bg-brand-500 text-white border border-brand-500 transition-all shadow-sm cursor-pointer disabled:opacity-50",
                                (isActive || singleSynthesizing === seg.id) ? "inline-flex" : "group-hover:inline-flex"
                              )}
                              title="Regenerate voice"
                            >
                              {singleSynthesizing === seg.id ? (
                                <Loader2 size={8} className="animate-spin" />
                              ) : (
                                <RefreshCw size={8} />
                              )}
                              <span>Regen</span>
                            </button>
                          </>
                        ) : (
                          <>
                            <span className={cn(
                              "inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-ink-800 text-ink-400 border border-ink-750 transition-all",
                              (isActive || singleSynthesizing === seg.id) ? "hidden" : "group-hover:hidden"
                            )}>
                              Pending
                            </span>
                            <button
                              onClick={() => handleSingleSynthesize(seg.id)}
                              disabled={singleSynthesizing !== null}
                              className={cn(
                                "hidden items-center gap-0.5 px-2 py-0.5 rounded-full text-[9px] font-bold bg-brand-600 hover:bg-brand-500 text-white border border-brand-500 transition-all shadow-sm cursor-pointer disabled:opacity-50",
                                (isActive || singleSynthesizing === seg.id) ? "inline-flex" : "group-hover:inline-flex"
                              )}
                              title="Generate voice"
                            >
                              {singleSynthesizing === seg.id ? (
                                <Loader2 size={8} className="animate-spin" />
                              ) : (
                                <Mic size={8} />
                              )}
                              <span>Generate</span>
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : inspectorMode === 'audio_clip_settings' ? (
          <div className="p-4 flex flex-col gap-4 text-xs bg-transparent h-full text-ink-300 select-none">
            {/* Multi-selection notice — effects below hit every selected clip */}
            {activeSeg && selectedSegmentIds.length > 1 && selectedSegmentIds.includes(activeSeg.id) && (
              <div className="flex items-center gap-1.5 px-2 py-1.5 rounded border border-brand-500/30 bg-brand-500/10 text-[10px] font-semibold text-brand-300">
                Changes apply to all {selectedSegmentIds.length} selected clips
              </div>
            )}
            {/* Horizontal Sub-tabs */}
            <div className="flex border-b border-ink-800 pb-1 gap-4">
              {(['basic', 'filter', 'speed'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveClipTab(tab)}
                  className={cn(
                    "pb-1 text-[11px] font-semibold uppercase tracking-wider relative transition-colors",
                    activeClipTab === tab ? "text-brand-400" : "text-ink-500 hover:text-ink-300"
                  )}
                >
                  {tab === 'basic' ? 'Basic' : tab === 'filter' ? 'Voice Filter' : 'Voice Speed'}
                  {activeClipTab === tab && (
                    <motion.div
                      layoutId="clipTabIndicator"
                      className="absolute bottom-0 left-0 right-0 h-[2px] bg-brand-500"
                      transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                    />
                  )}
                </button>
              ))}
            </div>

            {/* Tab contents */}
            <div className="flex-1 min-h-0 py-2">
              <AnimatePresence mode="wait">
                {activeClipTab === 'basic' && (
                  <motion.div
                    key="basic-tab"
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.15 }}
                    className="space-y-4"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-ink-400 flex items-center gap-1">
                        <Sliders size={12} /> Volume Level
                      </span>
                      <span className="font-mono text-brand-400 font-bold">{clipVolume > 0 ? `+${clipVolume}` : clipVolume} dB</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min="-20"
                        max="10"
                        step="1"
                        value={clipVolume}
                        onChange={(e) => setClipVolume(Number(e.target.value))}
                        // Saved on release — pointer or keyboard (arrow keys used to change it without saving)
                        onPointerUp={(e) => commitVolume(Number(e.currentTarget.value))}
                        onKeyUp={(e) => commitVolume(Number(e.currentTarget.value))}
                        className="flex-1 accent-brand-500 bg-ink-800 rounded-lg cursor-pointer h-1"
                      />
                      <button
                        onClick={() => {
                          setClipVolume(0)
                          if (activeSeg) handleVolumeChange(activeSeg.id, 0)
                        }}
                        className="px-2 py-0.5 rounded bg-ink-900 border border-ink-800 hover:border-ink-700 text-[10px] text-ink-400 hover:text-white transition-colors"
                      >
                        Reset
                      </button>
                    </div>
                  </motion.div>
                )}

                {activeClipTab === 'filter' && (
                  <motion.div
                    key="filter-tab"
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.15 }}
                    className="space-y-3"
                  >
                    <span className="font-semibold text-ink-400 block mb-1">Vocal Sound Profiles</span>
                    <div className="grid grid-cols-2 gap-2">
                      {[
                        { id: 'echo', name: 'Studio Echo', desc: 'Warm acoustic space' },
                        { id: 'synth', name: 'Robotic Synth', desc: 'Electronic voice tone' },
                        { id: 'bass', name: 'Deep Bass Booster', desc: 'Heavy low-end power' },
                        { id: 'phone', name: 'Telephone Effect', desc: 'Retro radio bandpass' },
                      ].map((filter) => {
                        const isSelected = clipFilter === filter.id
                        return (
                          <button
                            key={filter.id}
                            onClick={() => {
                              const newVal = clipFilter === filter.id ? null : filter.id
                              setClipFilter(newVal)
                              if (activeSeg) handleFilterChange(activeSeg.id, newVal)
                            }}
                            className={cn(
                              "p-2.5 rounded text-left border transition-all flex flex-col gap-0.5",
                              isSelected
                                ? "border-brand-500/50 bg-brand-500/5 text-brand-400 shadow-glow-sm"
                                : "border-ink-800/80 bg-ink-900/60 hover:border-ink-700 text-ink-300"
                            )}
                          >
                            <span className="font-semibold text-[11px]">{filter.name}</span>
                            <span className="text-[9px] text-ink-500">{filter.desc}</span>
                          </button>
                        )
                      })}
                    </div>
                  </motion.div>
                )}

                {activeClipTab === 'speed' && (
                  <motion.div
                    key="speed-tab"
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.15 }}
                    className="space-y-4"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-ink-400">Duration Speed</span>
                      <span className="font-mono text-brand-400 font-bold">{clipSpeed.toFixed(2)}x</span>
                    </div>
                    <input
                      type="range"
                      min="0.5"
                      max="2.0"
                      step="0.05"
                      value={clipSpeed}
                      onChange={(e) => setClipSpeed(Number(e.target.value))}
                      onPointerUp={(e) => commitSpeed(Number(e.currentTarget.value))}
                      onKeyUp={(e) => commitSpeed(Number(e.currentTarget.value))}
                      className="w-full accent-brand-500 bg-ink-800 rounded-lg cursor-pointer h-1"
                    />
                    <div className="flex gap-1.5">
                      {[0.5, 1.0, 1.5, 2.0].map((val) => (
                        <button
                          key={val}
                          onClick={() => {
                            setClipSpeed(val)
                            if (activeSeg) handleSpeedChange(activeSeg.id, val)
                          }}
                          className={cn(
                            "flex-1 py-1 rounded text-[10px] font-mono border transition-colors",
                            clipSpeed === val
                              ? "bg-brand-500/10 border-brand-500/30 text-brand-400"
                              : "bg-ink-900 border-ink-800 hover:border-ink-700 text-ink-400"
                          )}
                        >
                          {val.toFixed(1)}x
                        </button>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        ) : (
          /* Handle other modes: subtitle_settings */
          <div className="p-4 flex flex-col gap-4 text-xs bg-transparent h-full text-ink-300 select-none">
            <span className="font-semibold text-ink-400 block mb-1">Subtitle Styling</span>

            {/* Font Size Selector */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-ink-400">Font Size</span>
                <span className="font-mono text-brand-400 font-bold">{fontSize} px</span>
              </div>
              <input
                type="range"
                min="10"
                max="32"
                step="1"
                value={fontSize}
                onChange={(e) => setFontSize(Number(e.target.value))}
                className="w-full accent-brand-500 bg-ink-800 rounded-lg cursor-pointer h-1"
              />
            </div>

            {/* Text Alignment */}
            <div className="space-y-2.5">
              <span className="text-ink-400 block">Text Alignment</span>
              <div className="flex border border-ink-800 rounded overflow-hidden max-w-[200px]">
                {(['left', 'center', 'right'] as const).map((align) => {
                  const isSelected = textAlignment === align
                  return (
                    <button
                      key={align}
                      onClick={() => setTextAlignment(align)}
                      className={cn(
                        "flex-1 py-1 px-2.5 text-center font-medium capitalize text-[10px] transition-colors border-r last:border-r-0 border-ink-800",
                        isSelected
                          ? "bg-brand-500/10 text-brand-400 font-semibold"
                          : "bg-ink-900 text-ink-400 hover:text-ink-300"
                      )}
                    >
                      {align}
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="text-[10px] text-ink-500 leading-normal bg-ink-900/50 p-2.5 rounded border border-ink-900 mt-2">
              Applies to all subtitles
            </div>
          </div>
        )}
      </div>

      <Modal
        open={!!newSpeakerModal}
        onClose={() => { setNewSpeakerModal(null); setPickingNewSpeakerVoice(false) }}
        title={pickingNewSpeakerVoice ? undefined : 'New Speaker'}
        size={pickingNewSpeakerVoice ? 'xl' : 'sm'}
        className={pickingNewSpeakerVoice ? 'p-0 overflow-hidden' : undefined}
      >
        {pickingNewSpeakerVoice ? (
          <VoiceBrowser
            className="h-[min(70vh,600px)]"
            voices={availableVoices}
            currentVoiceId={newSpeakerVoice || null}
            usedBy={voiceUsers(jobSpeakers)}
            auto={{
              label: 'Choose for me',
              hint: "Fits the actor's voice",
            }}
            onChoose={(voiceId) => { setNewSpeakerVoice(voiceId ?? ''); setPickingNewSpeakerVoice(false) }}
            onClose={() => setPickingNewSpeakerVoice(false)}
            header={
              <div className="flex items-center gap-2 px-3 h-11 border-b border-ink-800/60 shrink-0">
                <button
                  onClick={() => setPickingNewSpeakerVoice(false)}
                  className="flex items-center gap-0.5 text-[11px] text-ink-400 hover:text-white transition-colors"
                >
                  <ChevronLeft size={14} /> New Speaker
                </button>
                <span className="text-[12px] font-semibold text-white ml-1 truncate">
                  Voice for {newSpeakerName.trim() || 'the new speaker'}
                </span>
              </div>
            }
          />
        ) : (
          <div className="space-y-4">
            <InputField
              label="Speaker Name"
              required
              value={newSpeakerName}
              onChange={(e) => setNewSpeakerName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleCreateSpeakerConfirm() }}
              placeholder="e.g. Narrator"
              autoFocus
            />
            <Field label="Voice">
              <VoiceTrigger
                voice={newSpeakerVoice ? availableVoices.find((v) => v.id === newSpeakerVoice) : undefined}
                emptyLabel="Choose for me"
                emptyHint="A voice that fits the actor is picked the first time it's voiced"
                playerId="new-speaker"
                onOpen={() => setPickingNewSpeakerVoice(true)}
                title="Choose this speaker's voice"
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setNewSpeakerModal(null)}>Cancel</Button>
              <Button onClick={handleCreateSpeakerConfirm} disabled={createSpeaker.isPending}>
                {createSpeaker.isPending ? 'Creating…' : 'Create & Assign'}
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {(() => {
        const seg = lineVoiceFor ? segments.find((s) => s.id === lineVoiceFor) : undefined
        const speaker = seg ? getSpeaker(seg) : undefined
        const speakerVoice = speaker?.voice_id ? availableVoices.find((v) => v.id === speaker.voice_id) : undefined
        return (
          <Modal open={!!seg} onClose={() => setLineVoiceFor(null)} size="xl" className="p-0 overflow-hidden">
            {seg && (
              <VoiceBrowser
                className="h-[min(70vh,600px)]"
                voices={availableVoices}
                currentVoiceId={seg.voice_id ?? null}
                usedBy={voiceUsers(jobSpeakers)}
                auto={{
                  label: !speaker ? 'Choose for me'
                    : speakerVoice ? `The speaker's voice (${speakerVoice.name})` : "The speaker's voice",
                  hint: !speaker ? "Fits the actor's voice" : undefined,
                }}
                onChoose={(voiceId) => {
                  setLineVoiceFor(null)
                  if ((seg.voice_id ?? null) !== voiceId) handleVoiceChange(seg.id, voiceId ?? '')
                }}
                onClose={() => setLineVoiceFor(null)}
                header={
                  <div className="flex items-center gap-2 px-3 h-11 border-b border-ink-800/60 shrink-0">
                    <span className="text-[12px] font-semibold text-white">Voice for this line</span>
                    <span className="text-[10.5px] text-ink-500 font-mono">{formatTime(seg.start_time)} – {formatTime(seg.end_time)}</span>
                    <button
                      onClick={() => setLineVoiceFor(null)}
                      aria-label="Close"
                      className="ml-auto flex items-center justify-center h-6 w-6 rounded text-ink-400 hover:text-white hover:bg-white/10"
                    >
                      <X size={13} />
                    </button>
                  </div>
                }
              />
            )}
          </Modal>
        )
      })()}
    </div>
  )
}
