// src/pages/EditorPage.tsx
// The main script editor — feels like a real video editing studio

import { useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ChevronLeft, ChevronRight, Mic2, Download, 
  Loader2, AlertCircle, Zap,
  PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen,
  Music,
} from 'lucide-react'
import { toast } from 'sonner'

import { useJob, useSegments, useSpeakers, useSynthesizeJob, useMixFinalAudio } from '@/hooks/useApi'
import { useEditorStore } from '@/store/editorStore'

import { VideoPlayer }    from '@/components/video/VideoPlayer'
import { TimelineEditor } from '@/components/timeline/TimelineEditor'
import { TranscriptPanel }from '@/components/transcript/TranscriptPanel'
import { SpeakerPanel }   from '@/components/speakers/SpeakerPanel'
import { Button }          from '@/components/ui/Button'

import { getJobStatusConfig, isJobRunning } from '@/lib/utils'

export default function EditorPage() {
  const { projectId, jobId } = useParams<{ projectId: string; jobId: string }>()
  const navigate = useNavigate()

  // Store
  const {
    duration, leftPanelCollapsed, rightPanelCollapsed,
    toggleLeftPanel, toggleRightPanel, resetEditor,
  } = useEditorStore()

  // Reset editor state on mount
  useEffect(() => {
    resetEditor()
  }, [jobId, resetEditor])

  // Server state
  const { data: job, isLoading: loadingJob } = useJob(jobId ?? null)
  const { data: segs = [], isLoading: loadingSegs } = useSegments(jobId ?? null)
  const { data: spks = [] } = useSpeakers(projectId ?? null)

  // Merge optimistic local segment position overrides from Zustand
  const { segmentPositions } = useEditorStore()
  const displaySegs = segs.map(s => {
    const pos = segmentPositions[s.id]
    return pos ? { ...s, ...pos } : s
  })

  const { mutate: synthesize, isPending: synthesizing } = useSynthesizeJob()
  const { mutate: mix,        isPending: mixing }        = useMixFinalAudio()

  const isRunning = job ? isJobRunning(job.status) : false
  const config    = job ? getJobStatusConfig(job.status) : null

  const handleSynthesize = () => {
    if (!jobId) return
    synthesize(jobId, {
      onSuccess: () => toast.success('TTS synthesis started!'),
      onError:   () => toast.error('Failed to start synthesis'),
    })
  }

  const handleMix = () => {
    if (!jobId) return
    mix(jobId, {
      onSuccess: () => toast.success('Audio mix started!'),
      onError:   () => toast.error('Failed to start mix'),
    })
  }

  const handleExport = () => {
    if (!job?.output_url) {
      toast.error('No dubbed video available yet. Run Mix first.')
      return
    }
    window.open(job.output_url, '_blank')
  }

  // Synthesize: only possible when there are approved segments
  const hasApprovedSegs = segs.some((s) => s.is_approved)
  // Mix: only possible when at least one segment has been synthesised
  const hasTtsAudio     = segs.some((s) => s.tts_audio_path !== '')

  const jobReady  = job?.status === 'completed' && segs.length > 0
  const canMix    = hasTtsAudio
  const canExport = !!job?.output_url
  const videoUrl  = job?.video_url ?? undefined

  return (
    <div className="h-screen w-screen flex flex-col bg-surface-0 overflow-hidden">
      {/* ─── Top Toolbar ─────────────────────────────────────────── */}
      <header
        className="h-10 shrink-0 flex items-center gap-1 px-2 border-b z-10"
        style={{ background: 'var(--color-surface-1)', borderColor: 'var(--color-border)' }}
      >
        {/* Back */}
        <button
          className="tool-btn"
          onClick={() => navigate(`/projects/${projectId}`)}
          title="Back to project"
        >
          <ChevronLeft size={15} />
        </button>

        <div className="w-px h-4 mx-1" style={{ background: 'var(--color-border)' }} />

        {/* Brand */}
        <div className="flex items-center gap-1.5 mr-2">
          <Zap size={12} className="text-brand-400" fill="currentColor" />
          <span className="text-[12px] font-bold text-white tracking-tight">
            Dubify<span className="text-brand-400">Studio</span>
          </span>
        </div>

        <div className="w-px h-4 mx-1" style={{ background: 'var(--color-border)' }} />

        {/* Breadcrumb */}
        <div className="flex items-center gap-1 text-[11px]">
          <Link to="/projects" className="text-white/35 hover:text-white transition-colors">Projects</Link>
          <ChevronRight size={10} className="text-white/20" />
          <Link to={`/projects/${projectId}`} className="text-white/35 hover:text-white transition-colors">Project</Link>
          <ChevronRight size={10} className="text-white/20" />
          <span className="text-white/60 font-mono">{jobId?.slice(0, 8)}</span>
        </div>

        {/* Center — job status */}
        <div className="flex-1 flex items-center justify-center gap-3">
          {isRunning && (
            <div className="flex items-center gap-1.5 text-[11px] text-brand-300">
              <Loader2 size={11} className="animate-spin" />
              <span>{config?.description ?? 'Processing…'}</span>
            </div>
          )}
          {job && config && (
            <div
              className="status-pip"
              style={{
                background: isRunning ? 'rgba(124,58,237,0.10)' : job.status === 'completed' ? 'rgba(16,185,129,0.08)' : 'rgba(255,255,255,0.05)',
                borderColor: isRunning ? 'rgba(124,58,237,0.25)' : job.status === 'completed' ? 'rgba(16,185,129,0.22)' : 'var(--color-border)',
                color: isRunning ? '#A78BFA' : job.status === 'completed' ? '#34D399' : 'var(--color-text-muted)',
              }}
            >
              {isRunning && <span className="inline-block h-1.5 w-1.5 rounded-full bg-brand-400 animate-pulse" />}
              {config.label}
            </div>
          )}
        </div>

        {/* Right — panel toggles + actions */}
        <div className="flex items-center gap-0.5">
          {/* Panel toggles */}
          <button className="tool-btn" onClick={toggleLeftPanel} title={leftPanelCollapsed ? 'Show speakers' : 'Hide speakers'}>
            {leftPanelCollapsed ? <PanelLeftOpen size={13} /> : <PanelLeftClose size={13} />}
          </button>
          <button className="tool-btn" onClick={toggleRightPanel} title={rightPanelCollapsed ? 'Show transcript' : 'Hide transcript'}>
            {rightPanelCollapsed ? <PanelRightOpen size={13} /> : <PanelRightClose size={13} />}
          </button>

          <div className="w-px h-4 mx-1" style={{ background: 'var(--color-border)' }} />

          {/* Synthesize */}
          <Button
            variant="accent"
            size="sm"
            onClick={handleSynthesize}
            loading={synthesizing}
            disabled={!jobReady || !hasApprovedSegs || synthesizing}
            icon={<Mic2 size={12} />}
            title={!hasApprovedSegs ? 'Approve segments first' : undefined}
          >
            Synthesize
          </Button>

          {/* Mix */}
          <Button
            variant="outline"
            size="sm"
            className="ml-1"
            onClick={handleMix}
            loading={mixing}
            disabled={!canMix || mixing}
            icon={<Music size={12} />}
            title={!canMix ? 'Run Synthesize first' : undefined}
          >
            Mix
          </Button>

          {/* Export */}
          <Button
            variant={canExport ? 'default' : 'ghost'}
            size="sm"
            className="ml-1"
            onClick={handleExport}
            disabled={!canExport}
            icon={<Download size={12} />}
          >
            Export
          </Button>
        </div>
      </header>


      {/* ─── Main Content ─────────────────────────────────────────── */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left sidebar — Speakers */}
        <AnimatePresence initial={false}>
          {!leftPanelCollapsed && (
            <motion.div
              className="h-full border-r border-border overflow-hidden shrink-0"
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 220, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ type: 'spring', stiffness: 400, damping: 40 }}
            >
              <SpeakerPanel
                speakers={spks}
                projectId={projectId!}
                className="h-full"
                segments={displaySegs}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Center — Video + Timeline */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {/* Video player area */}
          <div className="flex-1 min-h-0 flex items-center justify-center p-4 bg-black/30 relative overflow-hidden">
            {/* Cinematic vignette */}
            <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_center,transparent_60%,rgba(0,0,0,0.4)_100%)]" />

            {loadingJob ? (
              <div className="flex items-center gap-3 text-text-muted">
                <Loader2 size={20} className="animate-spin" />
                <span className="text-sm">Loading editor…</span>
              </div>
            ) : job?.status === 'failed' ? (
              <div className="flex flex-col items-center gap-3 text-center">
                <div className="h-14 w-14 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center">
                  <AlertCircle size={22} className="text-red-400" />
                </div>
                <p className="text-sm text-text-muted">Job failed: {job?.error_msg || 'Unknown error'}</p>
                <Button variant="outline" size="sm" onClick={() => navigate(`/projects/${projectId}`)} icon={<ChevronLeft size={13} />}>
                  Back to Project
                </Button>
              </div>
            ) : (
              <VideoPlayer
                videoUrl={videoUrl}
                segments={displaySegs}
                speakers={spks}
                className="max-h-full max-w-full aspect-video w-full"
              />
            )}
          </div>

          {/* Timeline */}
          <TimelineEditor
            segments={displaySegs}
            speakers={spks}
            duration={duration || (displaySegs.length > 0 ? Math.max(...displaySegs.map((s) => s.end_time)) + 5 : 60)}
            className="shrink-0 h-48"
          />
        </div>

        {/* Right sidebar — Transcript */}
        <AnimatePresence initial={false}>
          {!rightPanelCollapsed && (
            <motion.div
              className="h-full border-l border-border overflow-hidden shrink-0"
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 360, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ type: 'spring', stiffness: 400, damping: 40 }}
            >
              <TranscriptPanel
                segments={displaySegs}
                speakers={spks}
                jobId={jobId!}
                isLoading={loadingSegs}
                className="h-full"
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
