// src/components/transcript/TranscriptPanel.tsx
import { useRef, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { AlignLeft, Loader2, ChevronRight, Sliders, Volume2, Type, RefreshCw, Mic } from 'lucide-react'
import type { Segment, Speaker, Job } from '@/types'
import { useEditorStore, useActiveSegmentId } from '@/store/editorStore'
import { SegmentCardSkeleton } from '@/components/ui/Skeleton'
import { toast } from 'sonner'
import { cn, formatTime, getSpeakerColor, isJobRunning, getJobStatusConfig } from '@/lib/utils'
import { useQueryClient } from '@tanstack/react-query'
import { segments as segmentsApi } from '@/api/client'
import { useVoices, useSynthesizeSegment, useSynthesizeBatch } from '@/hooks/useApi'
 
interface TranscriptPanelProps {
  segments: Segment[]
  speakers: Speaker[]
  jobId: string
  isLoading?: boolean
  className?: string
  job?: Job
}
 
export function TranscriptPanel({
  segments, speakers, jobId, isLoading, className, job
}: TranscriptPanelProps) {
  const activeSegmentId = useActiveSegmentId()
  const {
    setCurrentTime,
    setRightPanelCollapsed,
    toggleSelectSegment,
    toggleSelectAllSegments,
    setInspectorMode,
    setFocusedTimelineItemId,
    setActiveSegment,
    updateSegmentText,
  } = useEditorStore()
 
  const selectedSegmentIds = useEditorStore((s) => s.selectedSegmentIds)
  const { data: availableVoices = [] } = useVoices()
  const inspectorMode = useEditorStore((s) => s.inspectorMode)
  const focusedTimelineItemId = useEditorStore((s) => s.focusedTimelineItemId)
 
  const qc = useQueryClient()
  const [singleSynthesizing, setSingleSynthesizing] = useState<string | null>(null)
  const [batchSynthesizing, setBatchSynthesizing] = useState(false)
  const synthesizeSegment = useSynthesizeSegment()
  const synthesizeBatch = useSynthesizeBatch()
 
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

  const handleResetInspector = () => {
    setInspectorMode('global_synthesis')
    setFocusedTimelineItemId(null)
  }
 
  const handleVoiceChange = async (segmentId: string, voiceId: string) => {
    try {
      await segmentsApi.update(segmentId, { voice_id: voiceId })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    } catch (err) {
      toast.error('Failed to assign voice')
      console.error(err)
    }
  }

  // Persist a text edit to the DB on blur so it survives reload / resume.
  // Changing the text also clears that segment's stale TTS clip (it was for the
  // old words) so it shows Pending and can be regenerated — other segments' voices
  // are untouched.
  const handleTextBlur = async (segmentId: string, text: string, original: string) => {
    if (text === original) return   // nothing changed
    try {
      await segmentsApi.update(segmentId, { khmer_text: text, tts_audio_path: '' })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    } catch (err) {
      toast.error('Failed to save edit')
      console.error(err)
    }
  }

  const handleSingleSynthesize = async (segmentId: string) => {
    setSingleSynthesizing(segmentId)
    try {
      await synthesizeSegment.mutateAsync({ segmentId, jobId })
      toast.success('Voice generated successfully ✓')
    } catch (err) {
      toast.error('Failed to generate voice')
      console.error(err)
    } finally {
      setSingleSynthesizing(null)
    }
  }

  const handleBatchSynthesize = async () => {
    setBatchSynthesizing(true)
    try {
      if (selectedSegmentIds.length === 1) {
        await synthesizeSegment.mutateAsync({ segmentId: selectedSegmentIds[0], jobId })
      } else {
        await synthesizeBatch.mutateAsync({ segmentIds: selectedSegmentIds, jobId })
      }
      toast.success(`Generated voice for ${selectedSegmentIds.length} segments ✓`)
      useEditorStore.setState({ selectedSegmentIds: [] })
    } catch (err) {
      toast.error('Failed to generate voice for selected segments')
      console.error(err)
    } finally {
      setBatchSynthesizing(false)
    }
  }

  // Active clip references
  const activeSeg = segments.find(s => s.id === (focusedTimelineItemId || activeSegmentId))
  const activeSpeaker = activeSeg ? getSpeaker(activeSeg) : null
  const activeSpeakerName = activeSpeaker?.name ?? activeSpeaker?.label ?? (activeSeg ? `Speaker ${getSpeakerIndex(activeSeg) + 1}` : 'Audio Clip')

  // Sync local states to the active segment parameters
  useEffect(() => {
    if (activeSeg) {
      setClipVolume(activeSeg.volume_db ?? 0)
      setClipFilter(activeSeg.voice_filter ?? null)
      setClipSpeed(activeSeg.voice_speed ?? 1.0)
    }
  }, [activeSeg?.id])

  const handleVolumeChange = async (segmentId: string, val: number) => {
    try {
      await segmentsApi.update(segmentId, { volume_db: val })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    } catch (err) {
      toast.error('Failed to update volume')
      console.error(err)
    }
  }

  const handleFilterChange = async (segmentId: string, val: string | null) => {
    try {
      await segmentsApi.update(segmentId, { voice_filter: val || "" })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    } catch (err) {
      toast.error('Failed to update voice filter')
      console.error(err)
    }
  }

  const handleSpeedChange = async (segmentId: string, val: number) => {
    try {
      await segmentsApi.update(segmentId, { voice_speed: val })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    } catch (err) {
      toast.error('Failed to update voice speed')
      console.error(err)
    }
  }

  return (
    <div
      className={cn('flex flex-col min-w-0 h-full bg-transparent', className)}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between gap-2 px-3 h-9 border-b border-zinc-800/50 shrink-0 bg-zinc-900"
      >
        <div className="flex items-center gap-1.5 min-w-0">
          {inspectorMode === 'global_synthesis' ? (
            <>
              <AlignLeft size={12} className="text-purple-400/90 animate-pulse" />
              <span className="text-[11px] font-bold text-purple-400/90 uppercase tracking-wider">
                Transcript Inspector
              </span>
            </>
          ) : (
            <>
              <button
                onClick={handleResetInspector}
                className="text-[10px] text-zinc-400 hover:text-white mr-1 flex items-center gap-0.5"
                title="Back to List"
              >
                <ChevronRight size={12} className="rotate-180" />
                <span>Back</span>
              </button>
              <div className="w-px h-3 bg-zinc-800/80 mx-1" />
              {inspectorMode === 'audio_clip_settings' ? (
                <Volume2 size={12} className="text-purple-400" />
              ) : (
                <Type size={12} className="text-purple-400" />
              )}
              <span className="text-[11px] font-bold text-zinc-200 uppercase tracking-wider truncate">
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
              onClick={() => setRightPanelCollapsed(true)}
              className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white transition-colors"
              title="Collapse transcript"
            >
              <ChevronRight size={14} />
            </button>
          )}
        </div>
      </div>


 
      {/* Selection Actions Toolbar */}
      <AnimatePresence>
        {selectedSegmentIds.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="px-3 py-2 border-b border-zinc-800/50 shrink-0 bg-purple-950/10 text-white flex items-center justify-between gap-2 overflow-hidden"
          >
            <div className="flex items-center gap-1.5 text-[10px] text-purple-600 dark:text-purple-300 font-bold">
              <span>{selectedSegmentIds.length} Selected</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleBatchSynthesize}
                disabled={batchSynthesizing}
                className="text-[10px] bg-purple-600 hover:bg-purple-500 text-white px-2.5 py-1 rounded font-semibold transition-all flex items-center gap-1 disabled:opacity-50 cursor-pointer"
              >
                {batchSynthesizing ? <Loader2 size={10} className="animate-spin" /> : <Mic size={10} />}
                <span>Generate Voice</span>
              </button>
              <button
                onClick={() => useEditorStore.setState({ selectedSegmentIds: [] })}
                className="text-[10px] text-zinc-500 hover:text-zinc-300 dark:text-zinc-400 dark:hover:text-white px-1.5 py-1 cursor-pointer"
              >
                Clear
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
 
      {/* Main content viewport */}
      <div ref={scrollRef} className="flex-1 overflow-auto bg-transparent">
        {isLoading ? (
          <div className="p-3 space-y-2">
            {[...Array(7)].map((_, i) => <SegmentCardSkeleton key={i} />)}
          </div>
        ) : segments.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center p-8 gap-3">
            {/* Stage 1 running — extracting / separating */}
            {job && (job.status === 'extracting' || job.status === 'separating' || job.status === 'pending') ? (
              <>
                <Loader2 size={24} className="text-brand-400 animate-spin mb-1" />
                <p className="text-[12px] font-semibold text-brand-300">
                  {job.status === 'separating' ? 'Splitting vocals from BGM…' : 'Extracting audio…'}
                </p>
                <p className="text-[11px] text-white/40 max-w-[200px] leading-normal">
                  Stems will appear on the timeline when ready
                </p>
                <div className="w-48 h-1 bg-zinc-800 rounded-full overflow-hidden mt-1">
                  <div className="h-full bg-brand-400 rounded-full transition-all duration-500"
                    style={{ width: `${job.progress ?? 0}%` }} />
                </div>
              </>
            ) : job?.status === 'stems_ready' ? (
              /* Stage 1 done — waiting for user to click Analyze */
              <>
                <div className="h-10 w-10 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mb-1">
                  <RefreshCw size={18} className="text-emerald-400" />
                </div>
                <p className="text-[12px] font-semibold text-emerald-400">Audio split complete</p>
                <p className="text-[11px] text-white/40 max-w-[200px] leading-normal">
                  Click the <span className="text-emerald-400 font-semibold">Analyze Speech</span> button on the Vocals track to detect speakers and transcribe.
                </p>
              </>
            ) : job && (job.status === 'diarizing' || job.status === 'transcribing' || job.status === 'translating') ? (
              /* Stage 2 running */
              <>
                <Loader2 size={24} className="text-brand-400 animate-spin mb-1" />
                <p className="text-[12px] font-semibold text-brand-300">
                  {job.status === 'diarizing'    ? 'Detecting speakers…'
                  : job.status === 'transcribing' ? 'Transcribing speech…'
                  : 'Translating dialogue…'}
                </p>
                <p className="text-[11px] text-white/40 max-w-[200px] leading-normal">
                  {job.status === 'diarizing'    ? 'pyannoteAI is identifying who speaks when'
                  : job.status === 'transcribing' ? 'Converting speech to text'
                  : 'Gemini is translating to Khmer'}
                </p>
                <div className="w-48 h-1.5 bg-zinc-800 rounded-full overflow-hidden mt-1">
                  <div className="h-full bg-brand-400 rounded-full transition-all duration-500"
                    style={{ width: `${job.progress ?? 0}%` }} />
                </div>
                <span className="text-[10px] text-white/25 font-mono">{job.progress ?? 0}%</span>
              </>
            ) : (
              <>
                <AlignLeft size={22} className="text-white/15 mb-1" />
                <p className="text-[12px] text-white/35 font-medium">No segments yet</p>
                <p className="text-[11px] text-white/20 mt-0.5">Upload a video to get started</p>
              </>
            )}
          </div>
        ) : inspectorMode === 'global_synthesis' ? (
          <table className="w-full text-left border-collapse text-[11px]">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/50 text-zinc-400 font-semibold select-none sticky top-0 z-10">
                <th className="p-2 w-8 text-center">
                  <input
                    type="checkbox"
                    className="rounded border-zinc-700 bg-zinc-800 text-purple-500 focus:ring-purple-500 focus:ring-offset-zinc-900 cursor-pointer h-3 w-3"
                    checked={segments.length > 0 && segments.every((s) => selectedSegmentIds.includes(s.id))}
                    onChange={() => toggleSelectAllSegments(segments.map((s) => s.id))}
                  />
                </th>
                <th className="p-2 w-20">Speaker</th>
                <th className="p-2 w-20">Time</th>
                <th className="p-2">Target Translation</th>
                <th className="p-2 w-28">Voice Clone</th>
                <th className="p-2 w-16 text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/40">
              {segments.map((seg) => {
                const speaker = getSpeaker(seg)
                const spkIdx = getSpeakerIndex(seg)
                const color = speaker?.color ?? getSpeakerColor(Math.max(0, spkIdx))
                const isSelected = selectedSegmentIds.includes(seg.id)
                const isActive = seg.id === activeSegmentId
                const isDone = !!seg.tts_audio_path

                return (
                  <tr
                    key={seg.id}
                    ref={(el) => {
                      if (el) cardRefs.current.set(seg.id, el)
                      else cardRefs.current.delete(seg.id)
                    }}
                    className={cn(
                      "group hover:bg-zinc-900/50 transition-colors cursor-pointer",
                      isActive && "bg-purple-500/15 dark:bg-purple-500/20 hover:bg-purple-500/20 dark:hover:bg-purple-500/25",
                      isSelected && "bg-zinc-900"
                    )}
                    onClick={() => {
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
                        className="rounded border-zinc-700 bg-zinc-800 text-purple-500 focus:ring-purple-500 focus:ring-offset-zinc-900 cursor-pointer h-3 w-3"
                        checked={isSelected}
                        onChange={() => toggleSelectSegment(seg.id)}
                      />
                    </td>
 
                    {/* Speaker */}
                    <td className="p-2 whitespace-nowrap">
                      <div className="flex items-center gap-1.5">
                        <span className="h-1.5 w-1.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
                        <span className={cn(
                          "truncate max-w-[70px] font-medium transition-colors",
                          isActive ? "text-purple-700 dark:text-purple-300 font-bold" : "text-zinc-300"
                        )}>
                          {speaker?.name ?? speaker?.label ?? `S${spkIdx + 1}`}
                        </span>
                      </div>
                    </td>
 
                    {/* Time */}
                    <td className={cn(
                      "p-2 whitespace-nowrap font-mono text-[10px] transition-colors",
                      isActive ? "text-purple-600 dark:text-purple-400 font-bold" : "text-zinc-400"
                    )}>
                      {formatTime(seg.start_time)} - {formatTime(seg.end_time)}
                    </td>
 
                    {/* Target Translation */}
                    <td className="p-2 whitespace-normal break-words text-zinc-200">
                      <textarea
                        value={seg.khmer_text || ''}
                        onChange={(e) => updateSegmentText(seg.id, e.target.value)}
                        onFocus={(e) => {
                          originalTextRef.current = e.target.value
                        }}
                        onBlur={(e) => handleTextBlur(seg.id, e.target.value, originalTextRef.current)}
                        onClick={(e) => {
                          e.stopPropagation()
                          setActiveSegment(seg.id)
                        }}
                        className={cn(
                          "w-full bg-transparent resize-none focus:outline-none focus:ring-1 focus:ring-purple-500/50 rounded-sm py-1 px-1.5 whitespace-normal break-words transition-colors",
                          isActive
                            ? "text-white font-semibold"
                            : "text-zinc-100"
                        )}
                        rows={2}
                      />
                    </td>

                    {/* Voice Clone Select */}
                    <td className="p-2" onClick={(e) => e.stopPropagation()}>
                      <select
                        className="bg-zinc-900 text-zinc-200 text-[10px] rounded border border-zinc-800 hover:border-zinc-700 hover:bg-zinc-800 py-0.5 px-1 focus:outline-none focus:border-purple-500/50 cursor-pointer w-full max-w-[100px] truncate"
                        value={seg.voice_id || ''}
                        onChange={(e) => handleVoiceChange(seg.id, e.target.value)}
                      >
                        <option value="">Default Voice</option>
                        {availableVoices.map((v) => (
                          <option key={v.id} value={v.id}>
                            {v.name}
                          </option>
                        ))}
                      </select>
                    </td>

                    {/* Status Badge */}
                    <td className="p-2 text-center whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-center min-h-[22px] relative">
                        {isDone ? (
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
                                "hidden items-center gap-0.5 px-2 py-0.5 rounded-full text-[9px] font-bold bg-purple-600 hover:bg-purple-500 text-white border border-purple-500 transition-all shadow-sm cursor-pointer disabled:opacity-50",
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
                              "inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-zinc-800 text-zinc-400 border border-zinc-750 transition-all",
                              (isActive || singleSynthesizing === seg.id) ? "hidden" : "group-hover:hidden"
                            )}>
                              Pending
                            </span>
                            <button
                              onClick={() => handleSingleSynthesize(seg.id)}
                              disabled={singleSynthesizing !== null}
                              className={cn(
                                "hidden items-center gap-0.5 px-2 py-0.5 rounded-full text-[9px] font-bold bg-purple-600 hover:bg-purple-500 text-white border border-purple-500 transition-all shadow-sm cursor-pointer disabled:opacity-50",
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
          <div className="p-4 flex flex-col gap-4 text-xs bg-transparent h-full text-zinc-300 select-none">
            {/* Horizontal Sub-tabs */}
            <div className="flex border-b border-zinc-800 pb-1 gap-4">
              {(['basic', 'filter', 'speed'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveClipTab(tab)}
                  className={cn(
                    "pb-1 text-[11px] font-semibold uppercase tracking-wider relative transition-colors",
                    activeClipTab === tab ? "text-purple-400" : "text-zinc-500 hover:text-zinc-300"
                  )}
                >
                  {tab === 'basic' ? 'Basic' : tab === 'filter' ? 'Voice Filter' : 'Voice Speed'}
                  {activeClipTab === tab && (
                    <motion.div
                      layoutId="clipTabIndicator"
                      className="absolute bottom-0 left-0 right-0 h-[2px] bg-purple-500"
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
                      <span className="font-semibold text-zinc-400 flex items-center gap-1">
                        <Sliders size={12} /> Volume Level
                      </span>
                      <span className="font-mono text-purple-400 font-bold">{clipVolume > 0 ? `+${clipVolume}` : clipVolume} dB</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min="-20"
                        max="10"
                        step="1"
                        value={clipVolume}
                        onChange={(e) => setClipVolume(Number(e.target.value))}
                        onMouseUp={() => activeSeg && handleVolumeChange(activeSeg.id, clipVolume)}
                        onTouchEnd={() => activeSeg && handleVolumeChange(activeSeg.id, clipVolume)}
                        className="flex-1 accent-purple-500 bg-zinc-800 rounded-lg cursor-pointer h-1"
                      />
                      <button
                        onClick={() => {
                          setClipVolume(0)
                          if (activeSeg) handleVolumeChange(activeSeg.id, 0)
                        }}
                        className="px-2 py-0.5 rounded bg-zinc-900 border border-zinc-800 hover:border-zinc-700 text-[10px] text-zinc-400 hover:text-white transition-colors"
                      >
                        Reset
                      </button>
                    </div>
                    <div className="text-[10px] text-zinc-500 leading-normal bg-zinc-900/50 p-2.5 rounded border border-zinc-900">
                      Adjust the volume of the synthetic audio clip. 0 dB represents original generated volume.
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
                    <span className="font-semibold text-zinc-400 block mb-1">Vocal Sound Profiles</span>
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
                                ? "border-purple-500/50 bg-purple-500/5 text-purple-400 shadow-glow-sm"
                                : "border-zinc-800/80 bg-zinc-900/60 hover:border-zinc-700 text-zinc-300"
                            )}
                          >
                            <span className="font-semibold text-[11px]">{filter.name}</span>
                            <span className="text-[9px] text-zinc-500">{filter.desc}</span>
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
                      <span className="font-semibold text-zinc-400">Duration Speed</span>
                      <span className="font-mono text-purple-400 font-bold">{clipSpeed.toFixed(2)}x</span>
                    </div>
                    <input
                      type="range"
                      min="0.5"
                      max="2.0"
                      step="0.05"
                      value={clipSpeed}
                      onChange={(e) => setClipSpeed(Number(e.target.value))}
                      onMouseUp={() => activeSeg && handleSpeedChange(activeSeg.id, clipSpeed)}
                      onTouchEnd={() => activeSeg && handleSpeedChange(activeSeg.id, clipSpeed)}
                      className="w-full accent-purple-500 bg-zinc-800 rounded-lg cursor-pointer h-1"
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
                              ? "bg-purple-500/10 border-purple-500/30 text-purple-400"
                              : "bg-zinc-900 border-zinc-800 hover:border-zinc-700 text-zinc-400"
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
          <div className="p-4 flex flex-col gap-4 text-xs bg-transparent h-full text-zinc-300 select-none">
            <span className="font-semibold text-zinc-400 block mb-1">Subtitle Styling</span>

            {/* Font Size Selector */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-zinc-400">Font Size</span>
                <span className="font-mono text-purple-400 font-bold">{fontSize} px</span>
              </div>
              <input
                type="range"
                min="10"
                max="32"
                step="1"
                value={fontSize}
                onChange={(e) => setFontSize(Number(e.target.value))}
                className="w-full accent-purple-500 bg-zinc-800 rounded-lg cursor-pointer h-1"
              />
            </div>

            {/* Text Alignment */}
            <div className="space-y-2.5">
              <span className="text-zinc-400 block">Text Alignment</span>
              <div className="flex border border-zinc-800 rounded overflow-hidden max-w-[200px]">
                {(['left', 'center', 'right'] as const).map((align) => {
                  const isSelected = textAlignment === align
                  return (
                    <button
                      key={align}
                      onClick={() => setTextAlignment(align)}
                      className={cn(
                        "flex-1 py-1 px-2.5 text-center font-medium capitalize text-[10px] transition-colors border-r last:border-r-0 border-zinc-800",
                        isSelected
                          ? "bg-purple-500/10 text-purple-400 font-semibold"
                          : "bg-zinc-900 text-zinc-400 hover:text-zinc-300"
                      )}
                    >
                      {align}
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="text-[10px] text-zinc-500 leading-normal bg-zinc-900/50 p-2.5 rounded border border-zinc-900 mt-2">
              Adjusting subtitle styles applies layout parameters globally across all translation clips.
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
