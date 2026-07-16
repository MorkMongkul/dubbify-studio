// src/pages/EditorPage.tsx
// The unified Dubify Studio — handles everything from video import through export.
//
// Left panel:   Sessions list + Import video
// Center:       Upload dropzone → pipeline progress → video player
// Right panel:  Transcript Inspector
// Bottom:       Timeline Editor

import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { useDropzone } from 'react-dropzone'
import {
  ChevronLeft, ChevronRight, Download,
  Loader2, AlertCircle, Zap, Scissors,
  UploadCloud, Film, FileText, X,
  Activity, VideoIcon, AlignLeft,
  FolderOpen, CheckCircle2, Trash2, Users,
} from 'lucide-react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'

import {
  useJob, useSegments, useSpeakers,
  useProjectJobs, useDeleteJob,
  useCreateProject, useUploadVideo, useUploadWithSubtitle,
  useAnalyzeJob, useMixFinalAudio,
  useUpdateSpeaker, useVoices,
} from '@/hooks/useApi'
import { useEditorStore } from '@/store/editorStore'
import { jobs as jobsApi } from '@/api/client'

import { VideoPlayer }    from '@/components/video/VideoPlayer'
import { TimelineEditor } from '@/components/timeline/TimelineEditor'
import { TranscriptPanel } from '@/components/transcript/TranscriptPanel'
import { Button } from '@/components/ui/Button'
import { PipelineStepper } from '@/features/upload/PipelineStepper'
import { LANGUAGE_OPTIONS } from '@/types'
import type { Job } from '@/types'
import { getJobStatusConfig, isJobRunning, getSpeakerDisplayName, getSpeakerColor, cn } from '@/lib/utils'

// Stage 1 status messages shown on the loading overlay
const STAGE1_MESSAGES: Record<string, string> = {
  pending:    'Preparing…',
  extracting: 'Extracting audio from video…',
  separating: 'Splitting vocals from background music…',
}

// ── Transcript placeholder (no job or stage 1 running) ───────────────────────
function TranscriptPlaceholder({ onCollapse }: { onCollapse?: () => void }) {
  return (
    <div className="h-full flex flex-col bg-transparent select-none">
      <div className="flex items-center justify-between gap-2 px-3 h-9 border-b border-zinc-800/50 shrink-0 bg-zinc-900">
        <div className="flex items-center gap-1.5">
          <AlignLeft size={12} className="text-purple-400/90" />
          <span className="text-[11px] font-bold text-purple-400/90 uppercase tracking-wider">Transcript</span>
        </div>
        {onCollapse && (
          <button onClick={onCollapse} className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white transition-colors">
            <ChevronRight size={14} />
          </button>
        )}
      </div>
      <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
        <div className="h-10 w-10 rounded-xl bg-neutral-bg3 border border-border flex items-center justify-center text-white/30 mb-3">
          <FileText size={16} />
        </div>
        <p className="text-xs font-semibold text-white/80 mb-1">No Script Transcript</p>
        <p className="text-[10px] text-text-muted max-w-[190px] leading-normal">
          Import a video and run the pipeline to generate dialogue and translations.
        </p>
      </div>
    </div>
  )
}

// ── Main EditorPage ───────────────────────────────────────────────────────────
export default function EditorPage() {
  const { projectId, jobId } = useParams<{ projectId: string; jobId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  // ── Store ─────────────────────────────────────────────────────────────────
  const {
    duration, rightPanelCollapsed,
    resetEditor, setRightPanelCollapsed,
    timelineHeight, setTimelineHeight,
    segmentPositions,
  } = useEditorStore()

  useEffect(() => { if (jobId) resetEditor() }, [jobId, resetEditor])

  // ── Resizable panels ──────────────────────────────────────────────────────
  const [leftPanelWidth,  setLeftPanelWidth]  = useState(470)
  const [rightPanelWidth, setRightPanelWidth] = useState(720)

  const leftDragRef  = useRef<{ startX: number; startWidth: number } | null>(null)
  const rightDragRef = useRef<{ startX: number; startWidth: number } | null>(null)
  const timelineDragRef = useRef<{ startY: number; startHeight: number } | null>(null)

  const handleLeftResizeDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    leftDragRef.current = { startX: e.clientX, startWidth: leftPanelWidth }
    const onMove = (ev: PointerEvent) => {
      if (!leftDragRef.current) return
      setLeftPanelWidth(Math.max(180, Math.min(480, leftDragRef.current.startWidth + (ev.clientX - leftDragRef.current.startX))))
    }
    const onUp = () => { leftDragRef.current = null; window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp) }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [leftPanelWidth])

  const handleRightResizeDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    rightDragRef.current = { startX: e.clientX, startWidth: rightPanelWidth }
    const onMove = (ev: PointerEvent) => {
      if (!rightDragRef.current) return
      setRightPanelWidth(Math.max(320, Math.min(720, rightDragRef.current.startWidth + (rightDragRef.current.startX - ev.clientX))))
    }
    const onUp = () => { rightDragRef.current = null; window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp) }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [rightPanelWidth])

  const handleTimelineResizeDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    timelineDragRef.current = { startY: e.clientY, startHeight: timelineHeight }
    const onMove = (ev: PointerEvent) => {
      if (!timelineDragRef.current) return
      setTimelineHeight(timelineDragRef.current.startHeight - (ev.clientY - timelineDragRef.current.startY))
    }
    const onUp = () => { timelineDragRef.current = null; window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp) }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [timelineHeight, setTimelineHeight])

  // ── Server state ──────────────────────────────────────────────────────────
  const { data: job,        isLoading: loadingJob  } = useJob(jobId ?? null)
  const { data: segs = [],  isLoading: loadingSegs } = useSegments(jobId ?? null)
  const { data: spks = []  } = useSpeakers(projectId ?? null)
  const { data: projectJobs = [], isLoading: loadingJobs, refetch: refetchJobs } = useProjectJobs(projectId ?? null)
  const { data: availableVoices = [] } = useVoices()
  const updateSpeaker = useUpdateSpeaker()
  const [editingSpeakerId, setEditingSpeakerId] = useState<string | null>(null)
  const [editingSpeakerName, setEditingSpeakerName] = useState('')

  // Invalidate segments/speakers when job status changes
  useEffect(() => {
    if (jobId && job?.status) {
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      if (projectId) qc.invalidateQueries({ queryKey: ['speakers', projectId] })
    }
  }, [job?.status, jobId, projectId, qc])

  // Auto-navigate to most recent job when opening a project with no job selected
  useEffect(() => {
    if (!projectId || jobId || loadingJobs) return
    if (projectJobs.length === 0) return
    const completed = projectJobs.filter(j => j.status === 'completed' || j.status === 'stems_ready')
    const target = completed.length > 0 ? completed[0] : projectJobs[0]
    navigate(`/projects/${projectId}/jobs/${target.id}`, { replace: true })
  }, [projectId, jobId, projectJobs, loadingJobs, navigate])

  // Merge optimistic segment position overrides.
  // For tts_audio_path: always prefer the server value if it exists — the local
  // override is only for the mock-simulation placeholder and must never hide a
  // real synthesised path that came back from the API.
  const displaySegs = segs.map(s => {
    const pos = segmentPositions[s.id]
    if (!pos) return s
    return {
      ...s,
      ...pos,
      tts_audio_path: s.tts_audio_path || pos.tts_audio_path || '',
      tts_duration_secs: s.tts_audio_path ? s.tts_duration_secs : (pos.tts_duration_secs ?? s.tts_duration_secs),
    }
  })

  // Speakers actually present in this session — shown in the left sidebar in
  // place of the Sessions list while a session is active.
  const sessionSpeakerIds = new Set(displaySegs.map(s => s.speaker_id).filter(Boolean))
  const sessionSpeakers = spks
    .filter(sp => sessionSpeakerIds.has(sp.id))
    .sort((a, b) => a.label.localeCompare(b.label))

  // Sync segmentPositions whenever server data changes:
  //   • New segments get an initial entry (timing + speaker only)
  //   • Existing entries get their tts_audio_path/duration updated when synthesis completes
  useEffect(() => {
    if (segs.length === 0) return
    const current = useEditorStore.getState().segmentPositions
    let updated = false
    const next = { ...current }
    segs.forEach(s => {
      const existing = current[s.id]
      if (!existing) {
        // First time we see this segment
        next[s.id] = {
          start_time: s.start_time,
          end_time: s.end_time,
          lane_index: s.lane_index ?? 0,
          tts_duration_secs: s.tts_duration_secs,
          tts_audio_path: s.tts_audio_path,
        }
        updated = true
      } else if (s.tts_audio_path && existing.tts_audio_path !== s.tts_audio_path) {
        // Synthesis completed — propagate the real audio path into positions
        next[s.id] = { ...existing, tts_audio_path: s.tts_audio_path, tts_duration_secs: s.tts_duration_secs }
        updated = true
      }
    })
    if (updated) useEditorStore.setState({ segmentPositions: next })
  }, [segs])

  // ── Mutations ─────────────────────────────────────────────────────────────
  const { mutate: analyze,   isPending: analyzing  } = useAnalyzeJob()
  const { mutate: mix,       isPending: mixing      } = useMixFinalAudio()
  const { mutate: deleteJob, isPending: deletingJob } = useDeleteJob()
  const { mutateAsync: createProject,  isPending: creatingProject  } = useCreateProject()
  const { mutateAsync: uploadVideo,    isPending: uploadingVideo   } = useUploadVideo()
  const { mutateAsync: uploadWithSub,  isPending: uploadingWithSub } = useUploadWithSubtitle()

  // ── Stage flags ───────────────────────────────────────────────────────────
  const isRunning    = job ? isJobRunning(job.status) : false
  const statusConfig = job ? getJobStatusConfig(job.status) : null
  const isStage1     = job?.status === 'pending' || job?.status === 'extracting' || job?.status === 'separating'
  const isStemsReady = job?.status === 'stems_ready'
  const isStage2     = job?.status === 'diarizing' || job?.status === 'transcribing' || job?.status === 'translating'

  // ── Upload state ──────────────────────────────────────────────────────────
  const [videoFile,       setVideoFile]       = useState<File | null>(null)
  const [subtitleFile,    setSubtitleFile]    = useState<File | null>(null)
  const [sessionName,     setSessionName]     = useState('')
  const [sourceLang,      setSourceLang]      = useState('zh')
  const [targetLang,      setTargetLang]      = useState('km')
  const [uploadProgress,  setUploadProgress]  = useState(0)
  const [uploadError,     setUploadError]     = useState<string | null>(null)
  const [exporting,       setExporting]       = useState(false)

  const isUploading    = uploadingVideo || uploadingWithSub
  const isSetupLoading = creatingProject || isUploading

  const onDropVideo = useCallback((files: File[]) => {
    const file = files[0]
    if (!file) return
    const allowed = ['video/mp4', 'video/mkv', 'video/webm', 'video/avi', 'video/mov', 'video/quicktime', 'video/x-matroska']
    if (!allowed.includes(file.type) && !file.name.match(/\.(mp4|mkv|webm|avi|mov)$/i)) {
      toast.error('Please upload a video file (MP4, MKV, WebM, AVI, MOV)')
      return
    }
    setVideoFile(file)
    setSessionName(`Dub: ${file.name.replace(/\.[^/.]+$/, '')}`)
    setUploadError(null)
    setUploadProgress(0)
  }, [])

  const onDropSubtitle = useCallback((files: File[]) => {
    const file = files[0]
    if (!file) return
    if (!file.name.match(/\.(srt|vtt|ass|ssa|sub)$/i)) {
      toast.error('Please upload a subtitle file (SRT, VTT, ASS)')
      return
    }
    setSubtitleFile(file)
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: onDropVideo,
    accept: { 'video/*': ['.mp4', '.mkv', '.webm', '.avi', '.mov'], 'video/x-matroska': ['.mkv'] },
    maxFiles: 1,
    disabled: isSetupLoading || !!jobId,
    noClick: true,
  })

  const { getRootProps: getSubRootProps, getInputProps: getSubInputProps, isDragActive: isSubDragActive } = useDropzone({
    onDrop: onDropSubtitle,
    accept: { 'text/*': ['.srt', '.vtt', '.ass', '.ssa', '.sub'] },
    maxFiles: 1,
    disabled: isSetupLoading || !videoFile,
  })

  const handleStartPipeline = async () => {
    if (!videoFile) { toast.error('Please select a video file first'); return }
    setUploadError(null)
    setUploadProgress(0)
    try {
      let currentProjectId = projectId
      const name = sessionName.trim() || `Dub: ${videoFile.name}`
      if (!currentProjectId) {
        const project = await createProject({ name, source_lang: sourceLang, target_lang: targetLang })
        currentProjectId = project.id
      }
      const interval = setInterval(() => setUploadProgress(p => { if (p >= 92) { clearInterval(interval); return p } return p + Math.random() * 8 }), 300)
      const res = subtitleFile
        ? await uploadWithSub({ projectId: currentProjectId!, video: videoFile, subtitle: subtitleFile })
        : await uploadVideo({ projectId: currentProjectId!, file: videoFile })
      clearInterval(interval)
      setUploadProgress(100)
      toast.success('Upload complete! Splitting audio…')
      setVideoFile(null); setSubtitleFile(null); setSessionName('')
      refetchJobs()
      navigate(`/projects/${currentProjectId}/jobs/${res.job_id}`)
    } catch (err) {
      setUploadProgress(0)
      const detail = (err as Error)?.message || 'Failed to start pipeline'
      setUploadError(detail)
      toast.error(detail, { duration: 6000 })
    }
  }

  const handleAnalyze = (maxSpeakers?: number) => {
    if (!jobId) return
    analyze({ jobId, maxSpeakers }, {
      onSuccess: () => toast.success('Analysis started — detecting speakers…'),
      onError:   () => toast.error('Failed to start analysis'),
    })
  }

  const handleExport = () => {
    if (!jobId) return
    setExporting(true)
    const toastId = toast.loading('Compiling dubbed video…')
    mix({ jobId, muteOriginal: false }, {
      onSuccess: async () => {
        await qc.invalidateQueries({ queryKey: ['job', jobId] })
        try {
          const freshJob = await jobsApi.get(jobId)
          if (freshJob.output_url) {
            toast.success('Done! Opening…', { id: toastId })
            window.open(freshJob.output_url, '_blank')
          } else {
            toast.error('Compiled but output URL missing', { id: toastId })
          }
        } catch { toast.error('Failed to retrieve video link', { id: toastId }) }
        finally { setExporting(false) }
      },
      onError: (err: any) => { toast.error(`Compilation failed: ${err.message}`, { id: toastId }); setExporting(false) },
    })
  }

  const handleDeleteJob = useCallback((e: React.MouseEvent, jId: string) => {
    e.stopPropagation()
    if (!projectId) return
    deleteJob({ jobId: jId, projectId }, {
      onSuccess: () => {
        toast.success('Session deleted')
        if (jId === jobId) navigate(`/projects/${projectId}`, { replace: true })
      },
      onError: () => toast.error('Failed to delete session'),
    })
  }, [projectId, jobId, deleteJob, navigate])

  const formatBytes = (b: number) => b < 1024 ? `${b} B` : b < 1048576 ? `${(b/1024).toFixed(1)} KB` : `${(b/1048576).toFixed(1)} MB`

  const hasTtsAudio  = segs.some(s => s.tts_audio_path !== '')
  const canExport    = hasTtsAudio && !exporting && !mixing
  const videoUrl     = job?.video_url ?? undefined
  const showTimeline = !!jobId && !loadingJob && !!job && !isStage1 && job.status !== 'failed'
  const showTranscript = !!jobId && !isStage1 && job?.status !== 'failed'

  // ── Center content renderers ───────────────────────────────────────────────
  const renderUploadArea = () => {
    if (videoFile) {
      return (
        <div className="glass-card max-w-lg w-full shadow-glow-sm overflow-hidden flex flex-col bg-neutral-bg2/90 border border-white/5 shrink-0">
          <div className="flex flex-col h-full w-full justify-between p-6">
            <div className="space-y-4">
              <div className="flex items-center gap-3 pb-3 border-b border-border">
                <div className="h-10 w-10 rounded-xl bg-emerald-500/10 border border-emerald-500/25 flex items-center justify-center shrink-0">
                  <Film size={18} className="text-emerald-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-white truncate">{videoFile.name}</p>
                  <p className="text-[10px] text-text-muted">{formatBytes(videoFile.size)}</p>
                </div>
                <button className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white" onClick={() => { setVideoFile(null); setSubtitleFile(null); setUploadProgress(0) }} disabled={isSetupLoading}>
                  <X size={14} />
                </button>
              </div>

              {/* Optional subtitle drop */}
              <div {...getSubRootProps()} className={cn('rounded-lg border border-dashed transition-all duration-150 cursor-pointer', isSubDragActive ? 'border-brand bg-brand/5' : subtitleFile ? 'border-brand-light/35 bg-brand/5' : 'border-border hover:border-border-strong hover:bg-white/3 bg-neutral-bg3')}>
                <input {...getSubInputProps()} />
                <div className="flex items-center gap-2.5 px-3 py-2">
                  <FileText size={14} className={subtitleFile ? 'text-brand-300' : 'text-text-muted'} />
                  <div className="flex-1 min-w-0 text-left">
                    {subtitleFile ? (
                      <span className="text-[11px] font-medium text-white truncate block">{subtitleFile.name}</span>
                    ) : (
                      <span className="text-[10px] text-text-secondary"><span className="text-brand-300 font-medium">Optional:</span> Drop subtitle track (.srt, .vtt, .ass)</span>
                    )}
                  </div>
                  {subtitleFile && <button className="p-0.5 rounded hover:bg-white/15 text-text-muted hover:text-white" onClick={(e) => { e.stopPropagation(); setSubtitleFile(null) }}><X size={12} /></button>}
                </div>
              </div>

              {/* Session name + language (always shown since we have a project) */}
              <div className="space-y-2">
                <div>
                  <label className="block text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-1">Session Name</label>
                  <input className="w-full h-8 px-2.5 rounded-lg border outline-none bg-neutral-bg3 text-xs text-text-primary border-border focus:border-brand" value={sessionName} onChange={e => setSessionName(e.target.value)} disabled={isSetupLoading} />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-1">Source</label>
                    <select className="w-full h-8 px-2.5 rounded-lg border outline-none bg-neutral-bg3 text-xs text-text-primary border-border focus:border-brand" value={sourceLang} onChange={e => setSourceLang(e.target.value)} disabled={isSetupLoading}>
                      {LANGUAGE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-1">Target</label>
                    <select className="w-full h-8 px-2.5 rounded-lg border outline-none bg-neutral-bg3 text-xs text-brand-300 font-semibold border-border focus:border-brand" value={targetLang} onChange={e => setTargetLang(e.target.value)} disabled={isSetupLoading}>
                      {LANGUAGE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-2.5 pt-3">
              {uploadError && (
                <div className="flex items-start gap-1.5 p-2 rounded border text-[10px] text-red-400 bg-red-500/5 border-red-500/10 text-left">
                  <AlertCircle size={12} className="shrink-0 mt-0.5" />
                  <span className="flex-1 leading-snug">{uploadError}</span>
                </div>
              )}
              <Button variant="default" size="sm" className="w-full font-semibold shadow-glow" onClick={handleStartPipeline} loading={isSetupLoading}>
                {isSetupLoading ? `Uploading… (${Math.round(uploadProgress)}%)` : 'Start Dubbing'}
              </Button>
            </div>
          </div>
        </div>
      )
    }

    return (
      <div className="flex flex-col items-center justify-center p-8 text-center gap-4 select-none">
        <div className="h-14 w-14 rounded-2xl bg-neutral-bg3 border border-border flex items-center justify-center text-brand-300 shadow-glow-sm">
          <UploadCloud size={24} />
        </div>
        <div className="space-y-1">
          <p className="text-sm font-bold text-white">Drop a video here to start</p>
          <p className="text-[11px] text-text-muted max-w-[220px] leading-normal">MP4, MKV, WebM, AVI, MOV supported</p>
        </div>
        <label className="inline-flex items-center justify-center h-8 px-4 rounded-lg border border-brand/20 bg-brand/5 hover:bg-brand/10 text-xs font-medium text-brand-300 cursor-pointer transition-colors">
          <input type="file" className="hidden" accept="video/mp4,video/mkv,video/webm,video/avi,video/mov" onChange={e => { const files = Array.from(e.target.files || []); if (files.length) onDropVideo(files) }} />
          Browse File
        </label>
      </div>
    )
  }

  const renderPipelineView = (activeJob: Job) => (
    <div className="flex flex-col h-full w-full justify-center p-6 text-center">
      <div className="max-w-md mx-auto w-full space-y-4">
        <div className="space-y-1">
          <div className="h-9 w-9 rounded-full bg-brand/10 border border-brand/20 flex items-center justify-center mx-auto text-brand-300 mb-1">
            <Activity size={16} className="animate-pulse" />
          </div>
          <h3 className="text-sm font-bold text-white">Processing…</h3>
          <p className="text-[10px] text-text-secondary leading-normal">{statusConfig?.description ?? 'Pipeline running'}</p>
        </div>
        <div className="text-left bg-neutral-bg3 rounded-lg border border-border p-3">
          <PipelineStepper job={activeJob} compact />
        </div>
      </div>
    </div>
  )

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="h-screen w-screen flex flex-col bg-surface-0 overflow-hidden text-white relative">
      <div {...getRootProps()} className="flex-1 flex flex-col min-h-0 overflow-hidden relative">
        <input {...getInputProps()} />

        {/* Global drag overlay */}
        <AnimatePresence>
          {isDragActive && (
            <motion.div className="absolute inset-0 bg-brand/10 border-2 border-dashed border-brand/80 backdrop-blur-xs z-50 flex flex-col items-center justify-center pointer-events-none" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
              <div className="glass-card px-8 py-6 rounded-2xl flex flex-col items-center gap-3 shadow-glow-lg border border-brand/30">
                <UploadCloud size={44} className="text-brand-300 animate-bounce" />
                <p className="text-base font-bold text-white">Drop video to import</p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Header ──────────────────────────────────────────────── */}
        <header className="h-10 shrink-0 flex items-center justify-between gap-1 px-3 border-b z-10" style={{ background: 'var(--color-surface-1)', borderColor: 'var(--color-border)' }} onClick={e => e.stopPropagation()}>
          <div className="flex items-center gap-2">
            <button className="tool-btn mr-1" onClick={() => navigate('/')} title="Home">
              <ChevronLeft size={14} />
            </button>
            <div className="flex items-center gap-1.5 select-none">
              <div className="h-5 w-5 rounded bg-brand flex items-center justify-center">
                <Zap size={11} className="text-white" fill="white" />
              </div>
              <span className="text-[12px] font-bold text-white tracking-tight">Dubify<span className="text-brand-300">Studio</span></span>
            </div>
            <div className="w-px h-4 mx-2" style={{ background: 'var(--color-border)' }} />
            <div className="flex items-center gap-1 text-[10px] text-text-muted">
              {jobId ? (
                <>
                  <button
                    onClick={() => navigate(`/projects/${projectId}`)}
                    className="hover:text-white transition-colors"
                    title="Back to sessions list"
                  >
                    Active Session
                  </button>
                  <ChevronRight size={10} className="opacity-40" /><span className="text-white font-mono">{jobId.slice(0, 8)}</span>
                </>
              ) : (
                <span className="text-brand-300 font-semibold tracking-wider uppercase text-[9px] px-1.5 py-0.5 rounded bg-brand/10 border border-brand/20">Editor</span>
              )}
            </div>
          </div>

          {/* Center: stage status hint — Stage 1 is already shown on the main
              panel's own processing card, so only Stage 2 needs a top-bar hint;
              stems_ready has no banner here either, the Vocals track's own
              Analyze button and the transcript panel's hint already cover it. */}
          <div className="flex-1 flex items-center justify-center gap-2">
            {isStage2 && (
              <div className="flex items-center gap-1.5 text-[11px] text-brand-300">
                <Loader2 size={11} className="animate-spin" />
                <span>{statusConfig?.description ?? 'Processing…'}</span>
              </div>
            )}
          </div>

          {/* Right: Export */}
          <Button
            variant={canExport ? 'default' : 'ghost'}
            size="sm"
            onClick={handleExport}
            loading={exporting || mixing}
            disabled={!canExport}
            icon={<Download size={11} />}
            className={cn('shadow-glow transition-all', canExport ? 'bg-brand text-white hover:bg-brand-hover' : '')}
          >
            Export
          </Button>
        </header>

        {/* ── Main bento layout ────────────────────────────────────── */}
        <div className="flex-1 flex flex-col min-h-0 w-full bg-zinc-950 overflow-hidden p-2 gap-2" onClick={e => e.stopPropagation()}>

          {/* Top row: Sessions | Video | Transcript */}
          <div className="flex-1 min-h-0 flex gap-2 overflow-hidden">

            {/* 1. Sessions Panel — becomes the Speaker list once a session is
                active; switch sessions via the "Active Session" breadcrumb above */}
            <div className="shrink-0 bg-zinc-900 rounded-lg border border-zinc-800/50 flex flex-col min-w-0 overflow-hidden" style={{ width: leftPanelWidth }}>
              {jobId ? (
                <>
                  <div className="h-9 border-b border-zinc-800/50 flex items-center gap-2 px-3 shrink-0">
                    <Users size={13} className="text-purple-400" />
                    <span className="text-xs font-semibold text-zinc-200">Speakers</span>
                  </div>
                  <div className="flex-1 overflow-y-auto p-3 space-y-2">
                    {sessionSpeakers.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-6 text-center">
                        <Users size={18} className="text-zinc-700 mb-2" />
                        <p className="text-[10px] text-zinc-600">No speakers yet</p>
                        <p className="text-[9px] text-zinc-700 mt-0.5">Appear here once segments are analyzed</p>
                      </div>
                    ) : (
                      sessionSpeakers.map((speaker, i) => {
                        const color = speaker.color ?? getSpeakerColor(i)
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
                                      updateSpeaker.mutate({ speakerId: speaker.id, data: { display_name: trimmed } })
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
                                    setEditingSpeakerName(speaker.display_name || getSpeakerDisplayName(speaker, i))
                                  }}
                                  title="Click to rename"
                                >
                                  {getSpeakerDisplayName(speaker, i)}
                                </span>
                              )}
                            </div>
                            <select
                              className="w-full bg-zinc-900 text-zinc-300 text-[10px] rounded border border-zinc-700/60 hover:border-zinc-600 py-1 px-1.5 focus:outline-none focus:border-purple-500/50 cursor-pointer"
                              value={speaker.voice_id || ''}
                              onChange={(e) => updateSpeaker.mutate({ speakerId: speaker.id, data: { voice_id: e.target.value || undefined } })}
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
                </>
              ) : (
                <>
                  <div className="h-9 border-b border-zinc-800/50 flex items-center gap-2 px-3 shrink-0">
                    <FolderOpen size={13} className="text-purple-400" />
                    <span className="text-xs font-semibold text-zinc-200">Sessions</span>
                  </div>
                  <div className="flex-1 overflow-y-auto p-3 space-y-3">
                    {/* Import new video */}
                    <label className="flex items-center gap-2 p-2 rounded border border-dashed border-zinc-700/60 hover:border-purple-500/50 hover:bg-purple-500/5 cursor-pointer transition-all group">
                      <input type="file" className="hidden" accept="video/mp4,video/mkv,video/webm,video/avi,video/mov" onChange={e => { const files = Array.from(e.target.files || []); if (files.length) onDropVideo(files) }} />
                      <div className="h-6 w-6 rounded-md bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0">
                        <UploadCloud size={11} className="text-purple-400" />
                      </div>
                      <span className="text-[11px] text-zinc-400 group-hover:text-zinc-200 transition-colors">Import new video</span>
                    </label>

                    {/* Job list */}
                    {loadingJobs && <div className="flex items-center gap-1.5 text-zinc-600 text-[10px] py-2"><Loader2 size={10} className="animate-spin" /> Loading…</div>}
                    {projectJobs.length > 0 && (
                      <div className="space-y-1">
                        <p className="text-[9px] font-semibold text-zinc-600 uppercase tracking-wider mb-1.5">Sessions</p>
                        {projectJobs.map(j => {
                          const isActive = j.id === jobId
                          const isDone = j.status === 'completed'
                          const isFailed = j.status === 'failed'
                          const isStemsReadyJob = j.status === 'stems_ready'
                          const filename = j.video_path ? j.video_path.split('/').pop() : 'Video'
                          const createdAt = new Date(j.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                          return (
                            <div key={j.id} onClick={() => navigate(`/projects/${projectId}/jobs/${j.id}`)} className={`group flex items-center gap-2 p-2 rounded cursor-pointer transition-all border ${isActive ? 'bg-purple-500/10 border-purple-500/30 text-white' : 'bg-zinc-800/30 border-zinc-800/40 hover:bg-zinc-800/60 hover:border-zinc-700/60'}`}>
                              <div className="shrink-0">
                                {isDone ? <CheckCircle2 size={13} className="text-emerald-400" />
                                  : isFailed ? <AlertCircle size={13} className="text-red-400" />
                                  : isStemsReadyJob ? <Scissors size={13} className="text-emerald-400" />
                                  : <Loader2 size={13} className="text-purple-400 animate-spin" />}
                              </div>
                              <div className="flex-1 min-w-0">
                                <p className={`text-[10px] font-medium truncate ${isActive ? 'text-white' : 'text-zinc-300'}`}>{filename}</p>
                                <p className="text-[9px] text-zinc-600">{createdAt}</p>
                              </div>
                              <button onClick={e => handleDeleteJob(e, j.id)} disabled={deletingJob} className={`shrink-0 p-1 rounded transition-all ${isActive ? 'opacity-60 hover:opacity-100 hover:bg-red-500/20 hover:text-red-400 text-zinc-400' : 'opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-red-500/20 hover:text-red-400 text-zinc-500'}`}>
                                <Trash2 size={11} />
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    )}
                    {!loadingJobs && projectJobs.length === 0 && (
                      <div className="flex flex-col items-center justify-center py-6 text-center">
                        <VideoIcon size={18} className="text-zinc-700 mb-2" />
                        <p className="text-[10px] text-zinc-600">No sessions yet</p>
                        <p className="text-[9px] text-zinc-700 mt-0.5">Import a video above</p>
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>

            {/* Left resize handle */}
            <div className="w-1 shrink-0 rounded-full cursor-col-resize hover:bg-purple-500/60 active:bg-purple-500 transition-colors group relative flex items-center justify-center" onPointerDown={handleLeftResizeDown}>
              <div className="w-0.5 h-8 rounded-full bg-zinc-700/60 group-hover:bg-purple-400/80 transition-colors" />
            </div>

            {/* 2. Center: Video/pipeline/upload */}
            <div className="flex-1 min-w-0 bg-zinc-900 rounded-lg border border-zinc-800/50 flex flex-col relative overflow-hidden">
              <div className="absolute inset-0 flex items-center justify-center p-6">
                {loadingJob && jobId ? (
                  <div className="flex flex-col items-center gap-3 text-text-muted">
                    <Loader2 size={22} className="animate-spin text-brand" />
                    <span className="text-xs">Loading session…</span>
                  </div>
                ) : jobId && job?.status === 'failed' ? (
                  <div className="flex flex-col items-center gap-3 text-center max-w-sm">
                    <div className="h-12 w-12 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400">
                      <AlertCircle size={20} />
                    </div>
                    <h4 className="text-xs font-bold text-white">Pipeline Failed</h4>
                    <p className="text-[10px] text-text-muted leading-normal">{job.error_msg || 'Unknown error'}</p>
                  </div>
                ) : jobId && job && isStage1 ? (
                  // Stage 1 running — show pipeline progress view
                  <div className="w-full max-w-[800px] aspect-video shrink-0 bg-zinc-900 relative flex items-center justify-center overflow-hidden">
                    {renderPipelineView(job)}
                  </div>
                ) : jobId && job && (isStemsReady || isStage2 || (!isRunning && !isStage1)) && videoUrl ? (
                  // Stems ready, Stage 2, or completed — show video player.
                  // No fixed aspect-ratio wrapper here: VideoPlayer sizes itself
                  // to the clip's real dimensions (landscape or portrait/Reel),
                  // bounded by the available pane space via max-w/max-h.
                  <VideoPlayer videoUrl={videoUrl} segments={displaySegs} speakers={spks} className="max-w-full max-h-full" jobId={jobId} projectId={projectId} jobStatus={job.status} />
                ) : jobId && job && !videoUrl ? (
                  <div className="flex flex-col items-center gap-4 text-center max-w-xs">
                    <Loader2 size={20} className="animate-spin text-brand-300" />
                    <p className="text-xs font-bold text-white">Preparing video…</p>
                  </div>
                ) : (
                  // No job — show upload UI
                  renderUploadArea()
                )}
              </div>

              {/* Floating re-open transcript button */}
              <AnimatePresence>
                {rightPanelCollapsed && (
                  <motion.button key="reopen" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 20 }} className="absolute top-1/2 -translate-y-1/2 right-0 z-20 h-24 w-6 bg-zinc-900 hover:bg-zinc-800 text-zinc-400 hover:text-white flex flex-col items-center justify-center gap-1 rounded-l border-y border-l border-zinc-800/50 transition-colors" onClick={() => setRightPanelCollapsed(false)}>
                    <ChevronLeft size={14} />
                    <span className="text-[9px] font-bold uppercase tracking-wider [writing-mode:vertical-lr] rotate-180 select-none">Script</span>
                  </motion.button>
                )}
              </AnimatePresence>
            </div>

            {/* Right resize handle */}
            <AnimatePresence initial={false}>
              {!rightPanelCollapsed && (
                <motion.div key="right-handle" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="w-1 shrink-0 rounded-full cursor-col-resize hover:bg-purple-500/60 active:bg-purple-500 transition-colors group relative flex items-center justify-center" onPointerDown={handleRightResizeDown}>
                  <div className="w-0.5 h-8 rounded-full bg-zinc-700/60 group-hover:bg-purple-400/80 transition-colors" />
                </motion.div>
              )}
            </AnimatePresence>

            {/* 3. Right: Transcript */}
            <AnimatePresence initial={false}>
              {!rightPanelCollapsed && (
                <motion.div key="transcript" initial={{ width: 0, opacity: 0 }} animate={{ width: rightPanelWidth, opacity: 1 }} exit={{ width: 0, opacity: 0 }} transition={{ type: 'spring', stiffness: 380, damping: 38 }} className="bg-zinc-900 rounded-lg border border-zinc-800/50 overflow-hidden flex flex-col shrink-0">
                  {showTranscript ? (
                    <TranscriptPanel
                      segments={displaySegs}
                      speakers={spks}
                      jobId={jobId!}
                      projectId={projectId!}
                      isLoading={loadingSegs}
                      className="h-full border-l-0"
                      job={job}
                    />
                  ) : (
                    <TranscriptPlaceholder onCollapse={() => setRightPanelCollapsed(true)} />
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Timeline resize handle */}
          <div className="h-1 shrink-0 rounded-full cursor-row-resize hover:bg-purple-500/60 active:bg-purple-500 transition-colors group relative flex items-center justify-center" onPointerDown={handleTimelineResizeDown}>
            <div className="w-8 h-0.5 rounded-full bg-zinc-700/60 group-hover:bg-purple-400/80 transition-colors" />
          </div>

          {/* Bottom: Timeline */}
          {showTimeline ? (
            <TimelineEditor
              segments={displaySegs}
              speakers={spks}
              duration={duration || (displaySegs.length > 0 ? Math.max(...displaySegs.map(s => s.end_time)) + 5 : 60)}
              className="bg-zinc-900 rounded-lg border border-zinc-800/50 overflow-hidden shrink-0"
              jobId={jobId}
              projectId={projectId}
              job={job}
              onAnalyze={handleAnalyze}
              analyzing={analyzing}
            />
          ) : (
            <div className="shrink-0 bg-zinc-900 rounded-lg border border-zinc-800/50 flex flex-col items-center justify-center text-center p-6" style={{ height: `${timelineHeight}px` }}>
              <div className="h-7 w-7 rounded-lg bg-neutral-bg3 border border-border/60 flex items-center justify-center text-white/20 mb-2">
                <Film size={13} />
              </div>
              <span className="text-[11px] font-semibold text-white/50">Timeline Editor</span>
              <span className="text-[10px] text-text-disabled mt-1">Import a video to initialize the timeline</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
