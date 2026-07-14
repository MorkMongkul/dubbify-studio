// src/pages/WorkspacePage.tsx
// The unified Studio Workspace page - handles file upload, processing, video preview, timeline, and export.

import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { useDropzone } from 'react-dropzone'
import {
  ChevronLeft, ChevronRight, Download,
  Loader2, AlertCircle, Zap, Scissors,
  UploadCloud, Film, FileText, X,
  Activity, VideoIcon, AlignLeft,
  FolderOpen, CheckCircle2, Trash2
} from 'lucide-react'
import { toast } from 'sonner'

import {
  useJob, useSegments, useSpeakers, useMixFinalAudio,
  useProjects, useCreateProject, useUploadVideo, useUploadWithSubtitle,
  useProjectJobs, useDeleteJob, useAnalyzeJob
} from '@/hooks/useApi'
import { useEditorStore } from '@/store/editorStore'
import { useQueryClient } from '@tanstack/react-query'
import { jobs as jobsApi } from '@/api/client'

import { VideoPlayer } from '@/components/video/VideoPlayer'
import { TimelineEditor } from '@/components/timeline/TimelineEditor'
import { TranscriptPanel } from '@/components/transcript/TranscriptPanel'
import { Button } from '@/components/ui/Button'
import { PipelineStepper } from '@/features/upload/PipelineStepper'
import { LANGUAGE_OPTIONS } from '@/types'
import type { Job } from '@/types'
import { getLanguageName, getJobStatusConfig, isJobRunning, cn } from '@/lib/utils'

// Placeholder when transcript is empty
function TranscriptPanelPlaceholder({ onCollapse }: { onCollapse?: () => void }) {
  return (
    <div className="h-full flex flex-col bg-transparent select-none">
      {/* Header */}
      <div
        className="flex items-center justify-between gap-2 px-3 h-9 border-b border-zinc-800/50 shrink-0 bg-zinc-900"
      >
        <div className="flex items-center gap-1.5">
          <AlignLeft size={12} className="text-purple-400/90" />
          <span className="text-[11px] font-bold text-purple-400/90 uppercase tracking-wider">Transcript</span>
        </div>
        {onCollapse && (
          <button
            onClick={onCollapse}
            className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white transition-colors"
            title="Collapse transcript"
          >
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
          Automated transcription and dialogue translations will appear here once processed.
        </p>
      </div>
    </div>
  )
}

export default function WorkspacePage() {
  const { projectId, jobId } = useParams<{ projectId: string; jobId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [exporting, setExporting] = useState(false)

  const {
    duration, rightPanelCollapsed,
    resetEditor, setRightPanelCollapsed,
    timelineHeight, setTimelineHeight
  } = useEditorStore()

  // Reset editor state on jobId change
  useEffect(() => {
    if (jobId) {
      resetEditor()
    }
  }, [jobId, resetEditor])

  // --- Panel resize state ---
  const [leftPanelWidth, setLeftPanelWidth] = useState(260)
  const [rightPanelWidth, setRightPanelWidth] = useState(480)
  const leftDragRef = useRef<{ startX: number; startWidth: number } | null>(null)
  const rightDragRef = useRef<{ startX: number; startWidth: number } | null>(null)

  const handleLeftResizeDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    leftDragRef.current = { startX: e.clientX, startWidth: leftPanelWidth }
    const onMove = (ev: PointerEvent) => {
      if (!leftDragRef.current) return
      const delta = ev.clientX - leftDragRef.current.startX
      setLeftPanelWidth(Math.max(180, Math.min(520, leftDragRef.current.startWidth + delta)))
    }
    const onUp = () => {
      leftDragRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [leftPanelWidth])

  const handleRightResizeDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    rightDragRef.current = { startX: e.clientX, startWidth: rightPanelWidth }
    const onMove = (ev: PointerEvent) => {
      if (!rightDragRef.current) return
      const delta = rightDragRef.current.startX - ev.clientX
      setRightPanelWidth(Math.max(320, Math.min(720, rightDragRef.current.startWidth + delta)))
    }
    const onUp = () => {
      rightDragRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [rightPanelWidth])

  const timelineDragRef = useRef<{ startY: number; startHeight: number } | null>(null)

  const handleTimelineResizeDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    timelineDragRef.current = { startY: e.clientY, startHeight: timelineHeight }
    const onMove = (ev: PointerEvent) => {
      if (!timelineDragRef.current) return
      const delta = ev.clientY - timelineDragRef.current.startY
      setTimelineHeight(timelineDragRef.current.startHeight - delta)
    }
    const onUp = () => {
      timelineDragRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [timelineHeight, setTimelineHeight])

  // --- API Query States (Active Session) ---
  const { data: job, isLoading: loadingJob } = useJob(jobId ?? null)
  const { data: segs = [], isLoading: loadingSegs } = useSegments(jobId ?? null)
  const { data: spks = [] } = useSpeakers(projectId ?? null)
  // All jobs for the current project (for left panel job list)
  const { data: projectJobs = [], isLoading: loadingProjectJobs, refetch: refetchJobs } = useProjectJobs(projectId ?? null)

  // Auto-redirect: if a project is open but no specific job is selected,
  // jump to the most recent job automatically so the editor opens right away.
  useEffect(() => {
    if (!projectId || jobId || loadingProjectJobs) return
    if (projectJobs.length === 0) return
    // Prefer most recent completed job, fallback to any most recent job
    const completed = projectJobs.filter(j => j.status === 'completed')
    const target = completed.length > 0 ? completed[0] : projectJobs[0]
    navigate(`/projects/${projectId}/jobs/${target.id}`, { replace: true })
  }, [projectId, jobId, projectJobs, loadingProjectJobs, navigate])


  // Merge optimistic local segment position overrides from Zustand
  const { segmentPositions } = useEditorStore()
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

  useEffect(() => {
    if (segs.length > 0) {
      const currentPositions = useEditorStore.getState().segmentPositions
      let updated = false
      const nextPositions = { ...currentPositions }

      segs.forEach(s => {
        const existing = currentPositions[s.id]
        if (!existing) {
          nextPositions[s.id] = {
            start_time: s.start_time,
            end_time: s.end_time,
            speaker_id: s.speaker_id ?? null,
            tts_duration_secs: s.tts_duration_secs,
            tts_audio_path: s.tts_audio_path,
          }
          updated = true
        } else if (s.tts_audio_path && existing.tts_audio_path !== s.tts_audio_path) {
          nextPositions[s.id] = { ...existing, tts_audio_path: s.tts_audio_path, tts_duration_secs: s.tts_duration_secs }
          updated = true
        }
      })

      if (updated) {
        useEditorStore.setState({ segmentPositions: nextPositions })
      }
    }
  }, [segs])

  const { mutate: mix, isPending: mixing } = useMixFinalAudio()
  const { mutate: analyze, isPending: analyzing } = useAnalyzeJob()

  const isRunning    = job ? isJobRunning(job.status) : false
  const statusConfig = job ? getJobStatusConfig(job.status) : null

  // Stage classification — drives which UI state to show
  const isStage1     = job?.status === 'pending' || job?.status === 'extracting' || job?.status === 'separating'
  const isStemsReady = job?.status === 'stems_ready'
  const isStage2     = job?.status === 'diarizing' || job?.status === 'transcribing' || job?.status === 'translating'

  const handleAnalyze = (maxSpeakers?: number) => {
    if (!jobId) return
    analyze({ jobId, maxSpeakers }, {
      onSuccess: () => toast.success('Analysis started — detecting speakers…'),
      onError:   () => toast.error('Failed to start analysis'),
    })
  }

  // --- Upload / Setup States ---
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [subtitleFile, setSubtitleFile] = useState<File | null>(null)
  const [sessionName, setSessionName] = useState('')
  const [sourceLang, setSourceLang] = useState('zh')
  const [targetLang, setTargetLang] = useState('kh')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const { data: allProjects } = useProjects()
  const { mutateAsync: createProject, isPending: creatingProject } = useCreateProject()
  const { mutateAsync: uploadVideo, isPending: uploadingVideo } = useUploadVideo()
  const { mutateAsync: uploadWithSub, isPending: uploadingWithSub } = useUploadWithSubtitle()
  const { mutate: deleteJob, isPending: deletingJob } = useDeleteJob()

  const isUploading = uploadingVideo || uploadingWithSub
  const isSetupLoading = creatingProject || isUploading

  // --- Handlers ---
  const handleDeleteJob = useCallback((e: React.MouseEvent, jId: string) => {
    e.stopPropagation() // don't navigate to the job on click
    if (!projectId) return
    deleteJob(
      { jobId: jId, projectId },
      {
        onSuccess: () => {
          toast.success('Session deleted')
          // If the deleted job was the active one, go back to project root
          if (jId === jobId) {
            navigate(`/projects/${projectId}`, { replace: true })
          }
        },
        onError: () => toast.error('Failed to delete session'),
      }
    )
  }, [projectId, jobId, deleteJob, navigate])
  const handleExport = () => {
    if (!jobId) return
    setExporting(true)
    const toastId = toast.loading('Compiling dubbed video... Please wait.')
    mix({ jobId, muteOriginal: false }, { // Keep BGM by default
      onSuccess: async () => {
        // Invalidate queries to get updated data
        await qc.invalidateQueries({ queryKey: ['job', jobId] })
        await qc.invalidateQueries({ queryKey: ['job'] })
        
        try {
          const freshJob = await jobsApi.get(jobId)
          if (freshJob.output_url) {
            toast.success('Video compiled successfully! Opening...', { id: toastId })
            window.open(freshJob.output_url, '_blank')
          } else {
            toast.error('Video compiled, but output URL is missing', { id: toastId })
          }
        } catch (err) {
          toast.error('Failed to retrieve the compiled video link', { id: toastId })
        } finally {
          setExporting(false)
        }
      },
      onError: (err: any) => {
        toast.error(`Compilation failed: ${err.message}`, { id: toastId })
        setExporting(false)
      }
    })
  }

  // Active session status flags
  const hasTtsAudio = segs.some((s) => s.tts_audio_path !== '')
  const canExport = hasTtsAudio && !exporting && !mixing
  const videoUrl = job?.video_url ?? undefined

  // --- Dropzone Settings ---
  const onDropVideo = useCallback((files: File[]) => {
    const file = files[0]
    if (!file) return
    const allowed = ['video/mp4', 'video/mkv', 'video/webm', 'video/avi', 'video/mov', 'video/quicktime', 'video/x-matroska']
    if (!allowed.includes(file.type) && !file.name.match(/\.(mp4|mkv|webm|avi|mov)$/i)) {
      toast.error('Please upload a video file (MP4, MKV, WebM, AVI, MOV)')
      return
    }
    setVideoFile(file)
    const baseName = file.name.substring(0, file.name.lastIndexOf('.')) || file.name
    setSessionName(`Dub: ${baseName}`)
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
    accept: {
      'video/*': ['.mp4', '.mkv', '.webm', '.avi', '.mov'],
      'video/x-matroska': ['.mkv'],
    },
    maxFiles: 1,
    disabled: isSetupLoading || !!jobId,
    noClick: true, // we handle browsing via an explicit button click
  })

  const { getRootProps: getSubRootProps, getInputProps: getSubInputProps, isDragActive: isSubDragActive } = useDropzone({
    onDrop: onDropSubtitle,
    accept: { 'text/*': ['.srt', '.vtt', '.ass', '.ssa', '.sub'] },
    maxFiles: 1,
    disabled: isSetupLoading || !videoFile,
  })

  // After a successful upload, refetch jobs and navigate to new job
  const handleStartPipeline = async () => {
    if (!videoFile) {
      toast.error('Please select a video file first')
      return
    }

    setUploadError(null)
    setUploadProgress(0)

    try {
      let currentProjectId = projectId
      let name = sessionName.trim() || `Dub: ${videoFile.name}`

      // If no project yet (root workspace), create one first
      if (!currentProjectId) {
        const project = await createProject({
          name,
          source_lang: sourceLang,
          target_lang: targetLang
        })
        currentProjectId = project.id
      }

      const interval = setInterval(() => {
        setUploadProgress((p) => {
          if (p >= 92) { clearInterval(interval); return p }
          return p + Math.random() * 8
        })
      }, 300)

      let res
      if (subtitleFile) {
        res = await uploadWithSub({
          projectId: currentProjectId!,
          video: videoFile,
          subtitle: subtitleFile
        })
      } else {
        res = await uploadVideo({
          projectId: currentProjectId!,
          file: videoFile
        })
      }

      clearInterval(interval)
      setUploadProgress(100)
      toast.success('Upload complete! AI pipeline starting.')

      setVideoFile(null)
      setSubtitleFile(null)
      setSessionName('')
      refetchJobs()

      navigate(`/projects/${currentProjectId}/jobs/${res.job_id}`)
    } catch (err) {
      setUploadProgress(0)
      const detail = (err as Error)?.message || 'Failed to start pipeline'
      setUploadError(detail)
      toast.error(detail, { duration: 6000 })
    }
  }

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  // --- Dynamic Sub-Views ---

  // Renders the tabbed upload settings/dropzone inside the video player grid block
  const renderSetupPlayerArea = () => {
    if (videoFile) {
      // Configuration & Start Pipeline view
      return (
        <div className="flex flex-col h-full w-full justify-between p-6">
          <div className="space-y-4">
            {/* Header info */}
            <div className="flex items-center gap-3 pb-3 border-b border-border">
              <div className="h-10 w-10 rounded-xl bg-emerald-500/10 border border-emerald-500/25 flex items-center justify-center shrink-0">
                <Film size={18} className="text-emerald-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-white truncate">{videoFile.name}</p>
                <p className="text-[10px] text-text-muted">{formatBytes(videoFile.size)}</p>
              </div>
              <button
                className="p-1 rounded hover:bg-white/10 text-text-muted hover:text-white"
                onClick={() => { setVideoFile(null); setSubtitleFile(null); setUploadProgress(0) }}
                disabled={isSetupLoading}
              >
                <X size={14} />
              </button>
            </div>

            {/* Optional Subtitle Dropzone */}
            <div
              {...getSubRootProps()}
              className={cn(
                'rounded-lg border border-dashed transition-all duration-150 cursor-pointer',
                isSubDragActive
                  ? 'border-brand bg-brand/5'
                  : subtitleFile
                    ? 'border-brand-light/35 bg-brand/5'
                    : 'border-border hover:border-border-strong hover:bg-white/3 bg-neutral-bg3',
              )}
            >
              <input {...getSubInputProps()} />
              <div className="flex items-center gap-2.5 px-3 py-2">
                <FileText size={14} className={subtitleFile ? 'text-brand-300' : 'text-text-muted'} />
                <div className="flex-1 min-w-0 text-left">
                  {subtitleFile ? (
                    <span className="text-[11px] font-medium text-white truncate block">{subtitleFile.name}</span>
                  ) : (
                    <span className="text-[10px] text-text-secondary">
                      <span className="text-brand-300 font-medium">Optional:</span> Drop subtitle track (.srt, .vtt, .ass)
                    </span>
                  )}
                </div>
                {subtitleFile && (
                  <button
                    className="p-0.5 rounded hover:bg-white/15 text-text-muted hover:text-white"
                    onClick={(e) => { e.stopPropagation(); setSubtitleFile(null) }}
                  >
                    <X size={12} />
                  </button>
                )}
              </div>
            </div>

            {/* Language & name — only shown when no project context (no projectId) */}
            {!projectId && (
              <div className="grid grid-cols-2 gap-3 text-left">
                <div className="col-span-2">
                  <label className="block text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-1">Session Name</label>
                  <input
                    className="w-full h-8 px-2.5 rounded-lg border outline-none bg-neutral-bg3 text-xs text-text-primary border-border focus:border-brand"
                    value={sessionName}
                    onChange={(e) => setSessionName(e.target.value)}
                    disabled={isSetupLoading}
                  />
                </div>
                <div>
                  <label className="block text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-1">Source Lang</label>
                  <select
                    className="w-full h-8 px-2.5 rounded-lg border outline-none bg-neutral-bg3 text-xs text-text-primary border-border focus:border-brand"
                    value={sourceLang}
                    onChange={(e) => setSourceLang(e.target.value)}
                    disabled={isSetupLoading}
                  >
                    {LANGUAGE_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-1">Target Lang</label>
                  <select
                    className="w-full h-8 px-2.5 rounded-lg border outline-none bg-neutral-bg3 text-xs text-brand-300 font-semibold border-border focus:border-brand"
                    value={targetLang}
                    onChange={(e) => setTargetLang(e.target.value)}
                    disabled={isSetupLoading}
                  >
                    {LANGUAGE_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                </div>
              </div>
            )}
          </div>

          {/* Action trigger */}
          <div className="space-y-2.5 pt-3">
            {uploadError && (
              <div className="flex items-start gap-1.5 p-2 rounded border text-[10px] text-red-400 bg-red-500/5 border-red-500/10 text-left">
                <AlertCircle size={12} className="shrink-0 mt-0.5" />
                <span className="flex-1 leading-snug">{uploadError}</span>
              </div>
            )}

            <Button
              variant="default"
              size="sm"
              className="w-full font-semibold shadow-glow"
              onClick={handleStartPipeline}
              loading={isSetupLoading}
            >
              {isSetupLoading
                ? `Processing... (${Math.round(uploadProgress)}%)`
                : 'Start AI Dubbing Pipeline'
              }
            </Button>
          </div>
        </div>
      )
    }

    // ── No video selected yet: show clean drop zone ──
    return (
      <div className="flex flex-col h-full w-full items-center justify-center p-6 text-center select-none gap-4">
        <div className="h-14 w-14 rounded-2xl bg-neutral-bg3 border border-border flex items-center justify-center text-brand-300 shadow-glow-sm">
          <UploadCloud size={24} />
        </div>
        <div className="space-y-1">
          <p className="text-sm font-bold text-white">Drop video here to start</p>
          <p className="text-[11px] text-text-muted max-w-[220px] leading-normal">
            MP4, MKV, WebM, AVI, MOV supported
          </p>
        </div>

        <label className="inline-flex items-center justify-center h-8 px-4 rounded-lg border border-brand/20 bg-brand/5 hover:bg-brand/10 text-xs font-medium text-brand-300 cursor-pointer select-none transition-colors">
          <input
            type="file"
            className="hidden"
            accept="video/mp4,video/mkv,video/webm,video/avi,video/mov"
            onChange={(e) => {
              const files = Array.from(e.target.files || [])
              if (files.length) onDropVideo(files)
            }}
          />
          <span>Browse File</span>
        </label>

        {/* Show recent projects only when no project context */}
        {!projectId && allProjects && allProjects.length > 0 && (
          <div className="w-full max-w-xs mt-2 space-y-1 text-left">
            <p className="text-[9px] font-semibold text-text-muted uppercase tracking-wider mb-1.5">Recent Projects</p>
            {allProjects.slice(0, 4).map((p) => (
              <div
                key={p.id}
                onClick={() => navigate(`/projects/${p.id}`)}
                className="group flex items-center justify-between p-2 rounded border border-border bg-neutral-bg3 hover:border-brand/40 cursor-pointer transition-all"
              >
                <div className="min-w-0 pr-2">
                  <p className="text-[11px] font-semibold text-white truncate group-hover:text-brand-300">{p.name}</p>
                  <p className="text-[9px] text-text-muted">{getLanguageName(p.source_lang)} → {getLanguageName(p.target_lang)}</p>
                </div>
                <ChevronRight size={12} className="text-text-muted group-hover:text-brand-300 shrink-0" />
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  // Renders the processing view inside the center video player frame
  const renderPipelinePlayerArea = (activeJob: Job) => {
    return (
      <div className="flex flex-col h-full w-full justify-center p-6 text-center">
        <div className="max-w-md mx-auto w-full space-y-4">
          <div className="space-y-1">
            <div className="h-9 w-9 rounded-full bg-brand/10 border border-brand/20 flex items-center justify-center mx-auto text-brand-300 mb-1">
              <Activity size={16} className="animate-pulse" />
            </div>
            <h3 className="text-sm font-bold text-white">AI Dubbing Pipeline Running</h3>
            <p className="text-[10px] text-text-secondary leading-normal">
              Whisper speech recognition and voice cloning task is active.
            </p>
          </div>

          <div className="text-left bg-neutral-bg3 rounded-lg border border-border p-3">
            <PipelineStepper job={activeJob} compact />
          </div>

          <div className="flex justify-center">
            <Button
              variant="outline"
              size="xs"
              icon={<ChevronLeft size={11} />}
              onClick={() => navigate('/')}
            >
              Cancel Workspace
            </Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen w-screen flex flex-col bg-surface-0 overflow-hidden text-white relative">

      {/* Global Drag and Drop Area Wrapper */}
      <div
        {...getRootProps()}
        className="flex-1 flex flex-col min-h-0 overflow-hidden relative"
      >
        <input {...getInputProps()} />

        {/* Global Drag and Drop Active Overlay */}
        <AnimatePresence>
          {isDragActive && (
            <motion.div
              className="absolute inset-0 bg-brand/10 border-2 border-dashed border-brand/80 backdrop-blur-xs z-50 flex flex-col items-center justify-center pointer-events-none"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
            >
              <div className="glass-card px-8 py-6 rounded-2xl flex flex-col items-center gap-3 shadow-glow-lg border border-brand/30">
                <UploadCloud size={44} className="text-brand-300 animate-bounce" />
                <div className="text-center space-y-1">
                  <p className="text-base font-bold text-white">Drop video file anywhere</p>
                  <p className="text-xs text-brand-300">to import and configure dubbing session</p>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ─── Top Header ─────────────────────────────────────────── */}
        <header
          className="h-10 shrink-0 flex items-center justify-between gap-1 px-3 border-b z-10"
          style={{ background: 'var(--color-surface-1)', borderColor: 'var(--color-border)' }}
          onClick={(e) => e.stopPropagation()} // don't trigger drop zone click
        >
          {/* Left Side: Branding / Navigation */}
          <div className="flex items-center gap-2">
            {jobId && (
              <button
                className="tool-btn mr-1.5"
                onClick={() => navigate('/')}
                title="Close Workspace"
              >
                <ChevronLeft size={14} />
              </button>
            )}

            <div className="flex items-center gap-1.5 select-none">
              <div className="h-5 w-5 rounded bg-brand flex items-center justify-center">
                <Zap size={11} className="text-white" fill="white" />
              </div>
              <span className="text-[12px] font-bold text-white tracking-tight">
                Dubify<span className="text-brand-300">Studio</span>
              </span>
            </div>

            <div className="w-px h-4 mx-2" style={{ background: 'var(--color-border)' }} />

            <div className="flex items-center gap-1 text-[10px] text-text-muted">
              {jobId ? (
                <>
                  <span>Active Session</span>
                  <ChevronRight size={10} className="opacity-40" />
                  <span className="text-white font-mono">{jobId.slice(0, 8)}</span>
                </>
              ) : (
                <span className="text-brand-300 font-semibold tracking-wider uppercase text-[9px] px-1.5 py-0.5 rounded bg-brand/10 border border-brand/20">
                  Upload
                </span>
              )}
            </div>
          </div>

          {/* Center Side: Active processing status indicator */}
          <div className="flex-1 flex items-center justify-center gap-2">
            {(isStage1 || isStage2) && (
              <div className="flex items-center gap-1.5 text-[11px] text-brand-300">
                <Loader2 size={11} className="animate-spin" />
                <span>{statusConfig?.description ?? 'Processing…'}</span>
              </div>
            )}
            {isStemsReady && (
              <div className="flex items-center gap-1.5 text-[11px] text-emerald-400">
                <Scissors size={11} />
                <span>Audio split complete — click <strong>Analyze Speech</strong> on the Vocals track</span>
              </div>
            )}
          </div>

          {/* Right Side: Panel Controls + Actions + Export */}
          <div className="flex items-center gap-1.5">
            {/* Export Button (Header Top Right) */}
            <Button
              variant={canExport ? 'default' : 'ghost'}
              size="sm"
              onClick={handleExport}
              loading={exporting || mixing}
              disabled={!canExport}
              icon={<Download size={11} />}
              className={cn("shadow-glow transition-all", canExport ? "bg-brand text-white hover:bg-brand-hover" : "")}
            >
              Export
            </Button>
          </div>
        </header>

        {/* ─── Main Editor: Bento Grid Canvas ─────────────────────── */}
        <div
          className="flex-1 flex flex-col min-h-0 w-full bg-zinc-950 overflow-hidden p-2 gap-2"
          onClick={(e) => e.stopPropagation()}
        >
          {/* ── TOP ROW: 3-Column Split (Project Media | Video Preview | Transcript/Inspector) ── */}
          <div className="flex-1 min-h-0 flex gap-2 overflow-hidden">

            {/* 1. Left Pane: Sessions Panel (resizable) */}
            <div
              className="shrink-0 bg-zinc-900 rounded-lg border border-zinc-800/50 flex flex-col min-w-0 overflow-hidden relative"
              style={{ width: leftPanelWidth }}
            >
              <div className="h-9 border-b border-zinc-800/50 flex items-center gap-2 px-3 shrink-0">
                <FolderOpen size={13} className="text-purple-400" />
                <span className="text-xs font-semibold text-zinc-200">Sessions</span>
              </div>
              <div className="flex-1 overflow-y-auto p-3 space-y-3">
                {/* Upload new session trigger */}
                <label
                  className="flex items-center gap-2 p-2 rounded border border-dashed border-zinc-700/60 hover:border-purple-500/50 hover:bg-purple-500/5 cursor-pointer transition-all group"
                >
                  <input
                    type="file"
                    className="hidden"
                    accept="video/mp4,video/mkv,video/webm,video/avi,video/mov"
                    onChange={(e) => {
                      const files = Array.from(e.target.files || [])
                      if (files.length) onDropVideo(files)
                    }}
                  />
                  <div className="h-6 w-6 rounded-md bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0">
                    <UploadCloud size={11} className="text-purple-400" />
                  </div>
                  <span className="text-[11px] text-zinc-400 group-hover:text-zinc-200 transition-colors">Import new video</span>
                </label>

                {/* Job list */}
                {loadingProjectJobs && (
                  <div className="flex items-center gap-1.5 text-zinc-600 text-[10px] py-2">
                    <Loader2 size={10} className="animate-spin" /> Loading sessions...
                  </div>
                )}

                {projectJobs.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-[9px] font-semibold text-zinc-600 uppercase tracking-wider mb-1.5">Sessions</p>
                    {projectJobs.map((j) => {
                      const isActive = j.id === jobId
                      const isDone = j.status === 'completed'
                      const isFailed = j.status === 'failed'
                      const filename = j.video_path ? j.video_path.split('/').pop() : 'Video'
                      const createdAt = new Date(j.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                      return (
                        <div
                          key={j.id}
                          onClick={() => navigate(`/projects/${projectId}/jobs/${j.id}`)}
                          className={`group flex items-center gap-2 p-2 rounded cursor-pointer transition-all border ${
                            isActive
                              ? 'bg-purple-500/10 border-purple-500/30 text-white'
                              : 'bg-zinc-800/30 border-zinc-800/40 hover:bg-zinc-800/60 hover:border-zinc-700/60'
                          }`}
                        >
                          {/* Status icon */}
                          <div className="shrink-0">
                            {isDone ? (
                              <CheckCircle2 size={13} className="text-emerald-400" />
                            ) : isFailed ? (
                              <AlertCircle size={13} className="text-red-400" />
                            ) : (
                              <Loader2 size={13} className="text-purple-400 animate-spin" />
                            )}
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className={`text-[10px] font-medium truncate ${isActive ? 'text-white' : 'text-zinc-300'}`}>
                              {filename}
                            </p>
                            <p className="text-[9px] text-zinc-600">{createdAt}</p>
                          </div>
                          {/* Delete button — visible on hover or when active */}
                          <button
                            onClick={(e) => handleDeleteJob(e, j.id)}
                            disabled={deletingJob}
                            title="Delete session"
                            className={`shrink-0 p-1 rounded transition-all ${
                              isActive
                                ? 'opacity-60 hover:opacity-100 hover:bg-red-500/20 hover:text-red-400 text-zinc-400'
                                : 'opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-red-500/20 hover:text-red-400 text-zinc-500'
                            }`}
                          >
                            <Trash2 size={11} />
                          </button>
                        </div>
                      )
                    })}
                  </div>
                )}

                {!loadingProjectJobs && projectJobs.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-6 text-center">
                    <VideoIcon size={18} className="text-zinc-700 mb-2" />
                    <p className="text-[10px] text-zinc-600">No sessions yet</p>
                    <p className="text-[9px] text-zinc-700 mt-0.5">Import a video to start</p>
                  </div>
                )}
              </div>
            </div>

            {/* Left panel resize handle */}
            <div
              className="w-1 shrink-0 rounded-full cursor-col-resize hover:bg-purple-500/60 active:bg-purple-500 transition-colors group relative flex items-center justify-center"
              onPointerDown={handleLeftResizeDown}
              title="Drag to resize"
            >
              <div className="w-0.5 h-8 rounded-full bg-zinc-700/60 group-hover:bg-purple-400/80 transition-colors" />
            </div>

            {/* 2. Center Pane: Video Preview Window */}
            <div className="flex-1 min-w-0 bg-zinc-900 rounded-lg border border-zinc-800/50 flex flex-col relative overflow-hidden">
              <div className="absolute inset-0 flex items-center justify-center p-6">

                {/* ── Center: state machine ── */}
                {loadingJob || (jobId && !job) ? (
                  // Job ID in URL but data not loaded yet
                  <div className="flex flex-col items-center gap-3 text-text-muted">
                    <Loader2 size={22} className="animate-spin text-brand" />
                    <span className="text-xs">Loading session…</span>
                  </div>
                ) : jobId && job?.status === 'failed' ? (
                  <div className="flex flex-col items-center gap-3 text-center max-w-sm">
                    <div className="h-12 w-12 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400">
                      <AlertCircle size={20} />
                    </div>
                    <h4 className="text-xs font-bold text-white">Pipeline Execution Failed</h4>
                    <p className="text-[10px] text-text-muted leading-normal">
                      Error detail: {job.error_msg || 'Unknown pipeline failure.'}
                    </p>
                    <Button variant="outline" size="xs" onClick={() => navigate(`/projects/${projectId}`)} icon={<ChevronLeft size={11} />}>
                      Back to Sessions
                    </Button>
                  </div>
                ) : jobId && job && isRunning ? (
                  <div className="w-full max-w-[800px] aspect-video shrink-0 bg-zinc-900 relative flex items-center justify-center overflow-hidden">
                    {renderPipelinePlayerArea(job)}
                  </div>
                ) : jobId && job && !isRunning && videoUrl ? (
                  <div className="w-full max-w-[800px] aspect-video shrink-0 bg-zinc-900 relative flex items-center justify-center overflow-hidden">
                    <VideoPlayer
                      videoUrl={videoUrl}
                      segments={displaySegs}
                      speakers={spks}
                      className="w-full h-full"
                    />
                  </div>
                ) : jobId && job && !isRunning && !videoUrl ? (
                  // Job completed but video URL not ready yet — show gentle waiting state
                  <div className="flex flex-col items-center gap-4 text-center max-w-xs">
                    <div className="h-12 w-12 rounded-2xl bg-brand/10 border border-brand/20 flex items-center justify-center">
                      <Loader2 size={20} className="animate-spin text-brand-300" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-xs font-bold text-white">Preparing video…</p>
                      <p className="text-[10px] text-text-muted">The pipeline finished — video is being finalised. This may take a few seconds.</p>
                    </div>
                  </div>
                ) : (
                  // No job at all — show the upload / dropzone UI
                  <div className="glass-card max-w-lg w-full h-[320px] shadow-glow-sm overflow-hidden flex flex-col bg-neutral-bg2/90 border border-white/5 shrink-0">
                    {renderSetupPlayerArea()}
                  </div>
                )}
              </div>

              {/* Floating Re-open Button when collapsed */}
              <AnimatePresence>
                {rightPanelCollapsed && (
                  <motion.button
                    key="reopen-transcript"
                    initial={{ opacity: 0, x: 20 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: 20 }}
                    className="absolute top-1/2 -translate-y-1/2 right-0 z-20 h-24 w-6 bg-zinc-900 hover:bg-zinc-800 text-zinc-400 hover:text-white flex flex-col items-center justify-center gap-1 rounded-l border-y border-l border-zinc-800/50 transition-colors"
                    onClick={() => setRightPanelCollapsed(false)}
                    title="Show transcript"
                  >
                    <ChevronLeft size={14} />
                    <span className="text-[9px] font-bold uppercase tracking-wider [writing-mode:vertical-lr] rotate-180 select-none">
                      Script
                    </span>
                  </motion.button>
                )}
              </AnimatePresence>
            </div>

            {/* Right panel resize handle */}
            <AnimatePresence initial={false}>
              {!rightPanelCollapsed && (
                <motion.div
                  key="right-resize-handle"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="w-1 shrink-0 rounded-full cursor-col-resize hover:bg-purple-500/60 active:bg-purple-500 transition-colors group relative flex items-center justify-center"
                  onPointerDown={handleRightResizeDown}
                  title="Drag to resize"
                >
                  <div className="w-0.5 h-8 rounded-full bg-zinc-700/60 group-hover:bg-purple-400/80 transition-colors" />
                </motion.div>
              )}
            </AnimatePresence>

            {/* 3. Right Pane: Inspector & Batch Table */}
            <AnimatePresence initial={false}>
              {!rightPanelCollapsed && (
                <motion.div
                  key="transcript-panel"
                  initial={{ width: 0, opacity: 0 }}
                  animate={{ width: rightPanelWidth, opacity: 1 }}
                  exit={{ width: 0, opacity: 0 }}
                  transition={{ type: 'spring', stiffness: 380, damping: 38 }}
                  className="bg-zinc-900 rounded-lg border border-zinc-800/50 overflow-hidden flex flex-col shrink-0"
                >
                  {jobId && !isRunning && job?.status !== 'failed' ? (
                    <TranscriptPanel
                      segments={displaySegs}
                      speakers={spks}
                      jobId={jobId!}
                      projectId={projectId!}
                      isLoading={loadingSegs}
                      className="h-full border-l-0"
                    />
                  ) : (
                    <TranscriptPanelPlaceholder onCollapse={() => setRightPanelCollapsed(true)} />
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Timeline vertical resize handle */}
          <div
            className="h-1 shrink-0 rounded-full cursor-row-resize hover:bg-purple-500/60 active:bg-purple-500 transition-colors group relative flex items-center justify-center"
            onPointerDown={handleTimelineResizeDown}
            title="Drag to resize"
          >
            <div className="w-8 h-0.5 rounded-full bg-zinc-700/60 group-hover:bg-purple-400/80 transition-colors" />
          </div>

          {/* ── BOTTOM ROW: Timeline Card ── */}
          {jobId && !loadingJob && job && !isRunning && job.status !== 'failed' ? (
            <TimelineEditor
              segments={displaySegs}
              speakers={spks}
              duration={duration || (displaySegs.length > 0 ? Math.max(...displaySegs.map((s) => s.end_time)) + 5 : 60)}
              className="bg-zinc-900 rounded-lg border border-zinc-800/50 overflow-hidden shrink-0"
            />
          ) : (
            <div
              className="shrink-0 bg-zinc-900 rounded-lg border border-zinc-800/50 flex flex-col items-center justify-center text-center p-6"
              style={{ height: `${timelineHeight}px` }}
            >
              <div className="h-7 w-7 rounded-lg bg-neutral-bg3 border border-border/60 flex items-center justify-center text-white/20 mb-2">
                <Film size={13} />
              </div>
              <span className="text-[11px] font-semibold text-white/50">Timeline Editor</span>
              <span className="text-[10px] text-text-disabled mt-1 max-w-xs leading-normal">
                Drop a video above or here to initialize timeline segments.
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
