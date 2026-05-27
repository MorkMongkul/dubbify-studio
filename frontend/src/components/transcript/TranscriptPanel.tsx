// src/components/transcript/TranscriptPanel.tsx
import { useRef, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { AlignLeft, CheckCheck, Loader2, ChevronRight, Sliders, Volume2, Type, RefreshCw, Mic } from 'lucide-react'
import type { Segment, Speaker } from '@/types'
import { useEditorStore, useActiveSegmentId } from '@/store/editorStore'
import { useApproveAll } from '@/hooks/useApi'
import { SegmentCardSkeleton } from '@/components/ui/Skeleton'
import { toast } from 'sonner'
import { cn, formatTime, getSpeakerColor } from '@/lib/utils'
import { useQueryClient } from '@tanstack/react-query'
import { tts, segments as segmentsApi } from '@/api/client'
 
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
  const availableVoices = useEditorStore((s) => s.availableVoices)
  const inspectorMode = useEditorStore((s) => s.inspectorMode)
  const focusedTimelineItemId = useEditorStore((s) => s.focusedTimelineItemId)
 
  const { mutate: approveAll, isPending: approvingAll } = useApproveAll()
  const qc = useQueryClient()
  const [singleSynthesizing, setSingleSynthesizing] = useState<string | null>(null)
  const [batchSynthesizing, setBatchSynthesizing] = useState(false)
 
  const scrollRef = useRef<HTMLDivElement>(null)
  const cardRefs = useRef<Map<string, HTMLTableRowElement>>(new Map())
 
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

  const approvedCount = segments.filter((s) => s.is_approved).length
  const totalCount = segments.length

  const handleApproveAll = () => {
    approveAll(jobId, {
      onSuccess: () => toast.success(`All ${totalCount} segments approved`),
      onError: () => toast.error('Failed to approve all'),
    })
  }

  const getSpeaker = (seg: Segment) => speakers.find((sp) => sp.id === seg.speaker_id)
  const getSpeakerIndex = (seg: Segment) => speakers.findIndex((sp) => sp.id === seg.speaker_id)

  const handleResetInspector = () => {
    setInspectorMode('global_synthesis')
    setFocusedTimelineItemId(null)
  }
 
  const handleSingleSynthesize = async (segmentId: string) => {
    setSingleSynthesizing(segmentId)
    try {
      const seg = segments.find(s => s.id === segmentId)
      if (seg && !seg.is_approved) {
        await segmentsApi.approve(segmentId)
      }
      await tts.synthesizeSegment(segmentId)
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      toast.success('Voice generated successfully ✓')
    } catch (err) {
      toast.error('Failed to generate voice')
      console.error(err)
    } finally {
      setSingleSynthesizing(null)
    }
  }
 
  const handleBatchApprove = async () => {
    try {
      await Promise.all(selectedSegmentIds.map(id => segmentsApi.approve(id)))
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      toast.success(`Approved ${selectedSegmentIds.length} segments ✓`)
      useEditorStore.setState({ selectedSegmentIds: [] })
    } catch (err) {
      toast.error('Failed to approve selected segments')
      console.error(err)
    }
  }
 
  const handleBatchSynthesize = async () => {
    setBatchSynthesizing(true)
    try {
      // Approve any unapproved segments in the batch first
      const unapprovedIds = selectedSegmentIds.filter(id => {
        const seg = segments.find(s => s.id === id)
        return seg && !seg.is_approved
      })
      if (unapprovedIds.length > 0) {
        await Promise.all(unapprovedIds.map(id => segmentsApi.approve(id)))
      }
 
      await Promise.all(selectedSegmentIds.map(id => tts.synthesizeSegment(id)))
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
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
              {totalCount > 0 && (
                <span className="text-[10px] font-mono text-zinc-500 font-bold">
                  {approvedCount}/{totalCount}
                </span>
              )}
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
          {inspectorMode === 'global_synthesis' ? (
            totalCount > 0 && approvedCount < totalCount && (
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
            )
          ) : (
            <button
              onClick={handleResetInspector}
              className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white transition-colors"
              title="Close Settings"
            >
              <ChevronRight size={14} className="rotate-90" />
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

      {/* Progress bar */}
      {totalCount > 0 && inspectorMode === 'global_synthesis' && (
        <div className="px-3 py-1.5 border-b border-zinc-800/50 shrink-0 bg-zinc-900">
          <div className="flex justify-between text-[10px] text-zinc-400 font-semibold mb-1">
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
                onClick={handleBatchApprove}
                className="text-[10px] bg-emerald-600 hover:bg-emerald-500 text-white px-2.5 py-1 rounded font-semibold transition-all flex items-center gap-1 cursor-pointer"
              >
                <CheckCheck size={10} />
                <span>Approve</span>
              </button>
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
          <div className="flex flex-col items-center justify-center h-full text-center p-8">
            <AlignLeft size={22} className="text-white/15 mb-3" />
            <p className="text-[12px] text-white/35">No segments yet</p>
            <p className="text-[11px] text-white/20 mt-1">Process a video to generate transcript</p>
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
                        onClick={(e) => {
                          e.stopPropagation()
                          setActiveSegment(seg.id)
                        }}
                        className={cn(
                          "w-full bg-transparent resize-none focus:outline-none focus:ring-1 focus:ring-purple-500/50 rounded-sm py-1 px-1.5 whitespace-normal break-words transition-colors",
                          isActive
                            ? "text-purple-900 dark:text-purple-200 font-semibold"
                            : "text-zinc-100"
                        )}
                        rows={2}
                      />
                    </td>

                    {/* Voice Clone Select */}
                    <td className="p-2" onClick={(e) => e.stopPropagation()}>
                      <select
                        className="bg-zinc-900 text-zinc-200 text-[10px] rounded border border-zinc-800 hover:border-zinc-700 hover:bg-zinc-800 py-0.5 px-1 focus:outline-none focus:border-purple-500/50 cursor-pointer w-full max-w-[100px] truncate"
                        value={seg.speaker_id || ''}
                        onChange={() => {
                          // Standard mock select handler
                        }}
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
                        className="flex-1 accent-purple-500 bg-zinc-800 rounded-lg cursor-pointer h-1"
                      />
                      <button
                        onClick={() => setClipVolume(0)}
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
                            onClick={() => setClipFilter(clipFilter === filter.id ? null : filter.id)}
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
                      className="w-full accent-purple-500 bg-zinc-800 rounded-lg cursor-pointer h-1"
                    />
                    <div className="flex gap-1.5">
                      {[0.5, 1.0, 1.5, 2.0].map((val) => (
                        <button
                          key={val}
                          onClick={() => setClipSpeed(val)}
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
