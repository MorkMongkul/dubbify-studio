// src/pages/EditorPage.tsx
// The unified Dubify Studio — handles everything from video import through export.
//
// Left panel:   Sessions list + Import video
// Center:       Upload dropzone → pipeline progress → video player
// Right panel:  Transcript Inspector
// Bottom:       Timeline Editor

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { useDropzone } from 'react-dropzone'
import {
  ChevronLeft, ChevronRight, Download,
  Loader2, AlertCircle, Zap,
  UploadCloud, Film, FileText, X,
  Activity, AlignLeft, ImagePlus, Edit2,
} from 'lucide-react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'

import {
  useJob, useSegments, useSpeakers,
  useProjectJobs,
  useCreateProject, useUploadVideo, useUploadWithSubtitle,
  useAnalyzeJob, useMixFinalAudio,
  useProject, useUpdateProject, useUploadProjectLogo, useDeleteProjectLogo,
  useOverlays,
} from '@/hooks/useApi'
import { useEditorStore } from '@/store/editorStore'

import { VideoPlayer }    from '@/components/video/VideoPlayer'
import { TimelineEditor } from '@/components/timeline/TimelineEditor'
import { TranscriptPanel } from '@/components/transcript/TranscriptPanel'
import { OverlaysPanel } from '@/components/overlays/OverlaysPanel'
import { EditorRail } from '@/components/layout/EditorRail'
import { SessionsPanel } from '@/components/panels/SessionsPanel'
import { SpeakersPanel } from '@/components/panels/SpeakersPanel'
import { ProjectsPanel } from '@/components/panels/ProjectsPanel'
import { VoicesPanel } from '@/components/panels/VoicesPanel'
import { SettingsPanel } from '@/components/panels/SettingsPanel'
import { PanelHeader, PanelEmptyState } from '@/components/panels/PanelShell'
import { Button } from '@/components/ui/Button'
import { Modal, InputField } from '@/components/ui/Modal'
import { PipelineStepper } from '@/features/upload/PipelineStepper'
import { LANGUAGE_OPTIONS } from '@/types'
import type { Job } from '@/types'
import { getJobStatusConfig, isJobRunning, cn } from '@/lib/utils'
import { chooseVideoSavePath, isDesktop } from '@/lib/desktop'
import { useHistoryStore } from '@/store/historyStore'

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
  // Per-field selectors, NOT a whole-store destructure: this component hosts
  // the entire editor tree, and subscribing to the whole store re-rendered
  // everything on every playhead tick (4×/s playing, 60+×/s scrubbing).
  const duration             = useEditorStore((s) => s.duration)
  const rightPanelCollapsed  = useEditorStore((s) => s.rightPanelCollapsed)
  const resetEditor          = useEditorStore((s) => s.resetEditor)
  const setRightPanelCollapsed = useEditorStore((s) => s.setRightPanelCollapsed)
  const timelineHeight       = useEditorStore((s) => s.timelineHeight)
  const setTimelineHeight    = useEditorStore((s) => s.setTimelineHeight)
  const segmentPositions     = useEditorStore((s) => s.segmentPositions)
  const togglePlaying        = useEditorStore((s) => s.togglePlaying)
  const railTab              = useEditorStore((s) => s.railTab)
  const setRailTab           = useEditorStore((s) => s.setRailTab)

  // Undo history is tied to one editing session — a stale entry from a
  // previous job could otherwise PATCH a segment that's no longer on screen.
  useEffect(() => { if (jobId) { resetEditor(); useHistoryStore.getState().clear() } }, [jobId, resetEditor])

  // Rail tab follows the routing context. Routes are keyed by pathname in
  // App.tsx, so every navigation remounts this page — running on mount IS
  // "derive on context change", and a manually chosen tab (e.g. Voices)
  // survives for as long as the URL stays put. railTab itself lives outside
  // resetEditor's wipe (see editorStore).
  useEffect(() => {
    if (jobId) setRailTab('speakers')
    else if (projectId) setRailTab('sessions')
    else setRailTab('projects')
  }, [projectId, jobId, setRailTab])

  // Space toggles play/pause, like every video editor — skipped while typing
  // in a text field so it doesn't hijack the spacebar from normal typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== 'Space' && e.key !== ' ') return
      const tag = (e.target as HTMLElement)?.tagName
      // SELECT/AUDIO: the dock hosts voice dropdowns and preview players now —
      // Space on those must operate the control, not toggle video playback.
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'AUDIO' || (e.target as HTMLElement)?.isContentEditable) return
      e.preventDefault()
      togglePlaying()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [togglePlaying])

  // Cmd/Ctrl+Z undo, Cmd/Ctrl+Shift+Z (or Ctrl+Y) redo — skipped while typing
  // in a text field so the browser's own native text-undo takes over there;
  // app-level undo only sees a text edit once it's committed on blur.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return
      const key = e.key.toLowerCase()
      if (key !== 'z' && key !== 'y') return
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'AUDIO' || (e.target as HTMLElement)?.isContentEditable) return
      e.preventDefault()
      const { past, future, undo, redo } = useHistoryStore.getState()
      if (key === 'y' || (key === 'z' && e.shiftKey)) {
        const label = future[future.length - 1]?.label
        if (label) redo().then(() => toast.message(`Redid: ${label}`))
      } else {
        const label = past[past.length - 1]?.label
        if (label) undo().then(() => toast.message(`Undid: ${label}`))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ── Resizable panels ──────────────────────────────────────────────────────
  const [leftPanelWidth,  setLeftPanelWidth]  = useState(400)
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
  const { data: currentProject } = useProject(projectId ?? null)
  const { data: jobOverlays = [] } = useOverlays(jobId ?? null)
  const updateProject = useUpdateProject()
  const uploadLogo = useUploadProjectLogo()
  const deleteLogo = useDeleteProjectLogo()
  // Inline project rename in the header — null = not editing.
  const [editingProjectName, setEditingProjectName] = useState<string | null>(null)

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
  // Memoized so child trees (timeline, transcript, player) keep stable segment
  // identities between unrelated re-renders — a fresh array of fresh objects
  // every render defeats React.memo everywhere downstream.
  const displaySegs = useMemo(() => segs.map(s => {
    const pos = segmentPositions[s.id]
    if (!pos) return s
    return {
      ...s,
      ...pos,
      tts_audio_path: s.tts_audio_path || pos.tts_audio_path || '',
      tts_duration_secs: s.tts_audio_path ? s.tts_duration_secs : (pos.tts_duration_secs ?? s.tts_duration_secs),
    }
  }), [segs, segmentPositions])

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
  const { mutateAsync: createProject,  isPending: creatingProject  } = useCreateProject()
  const { mutateAsync: uploadVideo,    isPending: uploadingVideo   } = useUploadVideo()
  const { mutateAsync: uploadWithSub,  isPending: uploadingWithSub } = useUploadWithSubtitle()

  // ── Stage flags ───────────────────────────────────────────────────────────
  const isRunning    = job ? isJobRunning(job.status) : false
  const statusConfig = job ? getJobStatusConfig(job.status) : null
  const isStage1     = job?.status === 'pending' || job?.status === 'extracting' || job?.status === 'separating'
  const isStemsReady = job?.status === 'stems_ready'
  const isStage2     = job?.status === 'diarizing' || job?.status === 'transcribing' || job?.status === 'translating'
  // Mixing runs in the background — the editor stays fully usable during it
  const isMixing     = job?.status === 'mixing'

  // ── Upload state ──────────────────────────────────────────────────────────
  const [videoFile,       setVideoFile]       = useState<File | null>(null)
  const [subtitleFile,    setSubtitleFile]    = useState<File | null>(null)
  const [sessionName,     setSessionName]     = useState('')
  const [sourceLang,      setSourceLang]      = useState('zh')
  const [targetLang,      setTargetLang]      = useState('km')
  const [uploadProgress,  setUploadProgress]  = useState(0)
  const [uploadError,     setUploadError]     = useState<string | null>(null)
  const exporting = mixing || isMixing
  const [logoUploading,   setLogoUploading]   = useState(false)

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

  // The mix runs as a backend background task: the POST returns 202 at once,
  // the job goes to `mixing`, and the regular job poll picks up completion.
  // `exporting` is derived (mutation in flight OR job mixing) — no local state
  // to keep in sync. The toast ref doubles as the "this tab started it" flag.
  const exportToastRef = useRef<string | number | null>(null)
  const exportPathRef = useRef<string | null>(null)
  // In-app destination picker, used whenever the native Save As dialog isn't
  // available. `reason` records why so the modal can explain itself.
  const [exportPrompt, setExportPrompt] = useState<{ path: string; reason: string } | null>(null)

  // Suggested filename: project name (or session id) + .mp4
  const suggestedExportName = `${(currentProject?.name || `dubify-${jobId?.slice(0, 8)}`)
    .replace(/[/\\:*?"<>|]/g, '-')
    .trim() || 'dubbed-video'}.mp4`

  const startMix = useCallback((destination: string) => {
    if (!jobId) return
    exportPathRef.current = destination
    exportToastRef.current = toast.loading(`Compiling dubbed video → ${destination.split('/').pop()}`)
    mix({ jobId, muteOriginal: false, exportPath: destination }, {
      // The backend echoes back the destination it actually resolved (with any
      // leading `~` expanded), so the success toast can name the real file.
      onSuccess: (data) => {
        if (data?.export_path) exportPathRef.current = data.export_path
      },
      onError: (err) => {
        toast.error(`Compilation failed: ${err.message}`, { id: exportToastRef.current ?? undefined })
        exportToastRef.current = null
        exportPathRef.current = null
      },
    })
  }, [jobId, mix])

  const handleExport = async () => {
    if (!jobId) return
    const target = await chooseVideoSavePath(suggestedExportName)
    if (target.kind === 'cancelled') return
    if (target.kind === 'path') {
      startMix(target.path)
      return
    }
    // No native picker available (plain browser, or the desktop dialog failed).
    // Ask in-app instead of exporting with no destination — that used to leave
    // the finished file buried in uploads/ and end in a window.open() the
    // desktop webview ignores, so Export looked like it did nothing at all.
    setExportPrompt({ path: `~/Downloads/${suggestedExportName}`, reason: target.reason })
  }

  const confirmExportPath = () => {
    const destination = exportPrompt?.path.trim()
    if (!destination) return
    setExportPrompt(null)
    startMix(destination)
  }

  // Watch for the mixing → completed transition. A failed mix returns the job
  // to `completed` with error_msg set (never `failed` — the session is fine).
  // Side effects only (toasts + opening the file); no state updates here.
  const prevJobStatusRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    const prev = prevJobStatusRef.current
    prevJobStatusRef.current = job?.status
    if (prev !== 'mixing' || job?.status !== 'completed' || exportToastRef.current == null) return
    const tid = exportToastRef.current
    const savedTo = exportPathRef.current
    exportToastRef.current = null
    exportPathRef.current = null
    if (job.error_msg) {
      // Covers "mix failed" and "mixed but couldn't write to your chosen path"
      toast.error(job.error_msg, { id: tid, duration: 10000 })
    } else if (savedTo) {
      toast.success(`Saved to ${savedTo}`, { id: tid, duration: 8000 })
    } else if (job.output_url) {
      toast.success('Done! Opening…', { id: tid })
      window.open(job.output_url, '_blank')
    } else {
      toast.error('Compiled but output URL missing', { id: tid })
    }
  }, [job, job?.status])

  const handleLogoFileChange = (file: File | null) => {
    if (!file || !projectId) return
    setLogoUploading(true)
    uploadLogo.mutate({ projectId, file }, {
      onSuccess: () => toast.success('Logo uploaded'),
      onError: () => toast.error('Failed to upload logo'),
      onSettled: () => setLogoUploading(false),
    })
  }

  const handleRemoveLogo = () => {
    if (!projectId) return
    deleteLogo.mutate(projectId, {
      onSuccess: () => toast.success('Logo removed'),
      onError: () => toast.error('Failed to remove logo'),
    })
  }

  // Commit the header's inline project rename. No-ops on blank/unchanged so
  // blur-after-Escape and click-away-without-typing never fire a PATCH.
  const commitProjectRename = () => {
    const trimmed = (editingProjectName ?? '').trim()
    setEditingProjectName(null)
    if (!projectId || !currentProject || !trimmed || trimmed === currentProject.name) return
    updateProject.mutate(
      { id: projectId, data: { name: trimmed } },
      {
        onSuccess: () => toast.success('Project renamed'),
        onError: () => toast.error('Failed to rename project'),
      }
    )
  }

  const formatBytes = (b: number) => b < 1024 ? `${b} B` : b < 1048576 ? `${(b/1024).toFixed(1)} KB` : `${(b/1048576).toFixed(1)} MB`

  const hasTtsAudio  = segs.some(s => s.tts_audio_path !== '')
  const canExport    = hasTtsAudio && !exporting
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
    <div className="h-screen w-screen flex bg-surface-0 overflow-hidden text-white relative">
      {/* CapCut-style icon rail — full height, drives the dynamic dock */}
      <EditorRail />
      <div {...getRootProps()} className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden relative">
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
          <div className="flex items-center gap-2 min-w-0">
            <div className="flex items-center gap-1.5 select-none shrink-0">
              <div className="h-5 w-5 rounded bg-brand flex items-center justify-center">
                <Zap size={11} className="text-white" fill="white" />
              </div>
              <span className="text-[12px] font-bold text-white tracking-tight">Dubify<span className="text-brand-300">Studio</span></span>
            </div>
            <div className="w-px h-4 mx-2 shrink-0" style={{ background: 'var(--color-border)' }} />
            {projectId && currentProject ? (
              // Project identity — CapCut-style inline rename on click.
              <div className="flex items-center gap-1.5 min-w-0">
                {editingProjectName !== null ? (
                  <input
                    autoFocus
                    className="bg-zinc-950 text-[11.5px] font-semibold text-white rounded px-1.5 py-0.5 w-56 focus:outline-none border border-purple-500/50"
                    value={editingProjectName}
                    onChange={(e) => setEditingProjectName(e.target.value)}
                    onBlur={commitProjectRename}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') e.currentTarget.blur()
                      if (e.key === 'Escape') setEditingProjectName(null)
                    }}
                  />
                ) : (
                  <button
                    className="group/name flex items-center gap-1.5 min-w-0"
                    onClick={() => setEditingProjectName(currentProject.name)}
                    title="Click to rename project"
                  >
                    <span className="text-[11.5px] font-semibold text-white truncate max-w-[240px]">{currentProject.name}</span>
                    <Edit2 size={10} className="shrink-0 text-white/25 group-hover/name:text-white/60 transition-colors" />
                  </button>
                )}
                {jobId && (
                  <button
                    onClick={() => setRailTab('sessions')}
                    className="text-[9.5px] font-mono text-text-muted hover:text-white px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 transition-colors shrink-0"
                    title="Current session — click to browse sessions"
                  >
                    {jobId.slice(0, 8)}
                  </button>
                )}
              </div>
            ) : (
              <span className="text-brand-300 font-semibold tracking-wider uppercase text-[9px] px-1.5 py-0.5 rounded bg-brand/10 border border-brand/20">Editor</span>
            )}
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
            loading={exporting}
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

            {/* 1. Dynamic dock — content driven by the icon rail's selected tab */}
            <div className="shrink-0 bg-zinc-900 rounded-lg border border-zinc-800/50 flex flex-col min-w-0 overflow-hidden" style={{ width: leftPanelWidth }}>
              {railTab === 'projects' ? (
                <ProjectsPanel activeProjectId={projectId} />
              ) : railTab === 'sessions' ? (
                <SessionsPanel
                  projectId={projectId}
                  activeJobId={jobId}
                  onImportFile={onDropVideo}
                  onShowProjects={() => setRailTab('projects')}
                />
              ) : railTab === 'speakers' ? (
                <SpeakersPanel projectId={projectId} jobId={jobId} />
              ) : railTab === 'elements' ? (
                jobId ? (
                  <>
                    <PanelHeader icon={ImagePlus} title="Elements" />
                    <div className="flex-1 overflow-y-auto p-3">
                      <OverlaysPanel
                        jobId={jobId}
                        project={currentProject}
                        onUploadProjectLogo={handleLogoFileChange}
                        onRemoveProjectLogo={handleRemoveLogo}
                        projectLogoUploading={logoUploading}
                      />
                    </div>
                  </>
                ) : (
                  <div className="h-full flex flex-col min-h-0">
                    <PanelHeader icon={ImagePlus} title="Elements" />
                    <PanelEmptyState
                      icon={ImagePlus}
                      title="No session open"
                      hint="Overlays — logos, subtitles, cover boxes — attach to a session's video."
                    />
                  </div>
                )
              ) : railTab === 'voices' ? (
                <VoicesPanel />
              ) : (
                <SettingsPanel />
              )}
            </div>

            {/* Left resize handle */}
            <div className="w-1 shrink-0 rounded-full cursor-col-resize hover:bg-purple-500/60 active:bg-purple-500 transition-colors group relative flex items-center justify-center" onPointerDown={handleLeftResizeDown}>
              <div className="w-0.5 h-8 rounded-full bg-zinc-700/60 group-hover:bg-purple-400/80 transition-colors" />
            </div>

            {/* 2. Center: Video/pipeline/upload */}
            <div className="flex-1 min-w-0 bg-zinc-900 rounded-lg border border-zinc-800/50 flex flex-col relative overflow-hidden">
              <div className="absolute inset-0 flex items-center justify-center p-6">
                {videoFile ? (
                  // A newly staged import always wins the center pane — the
                  // Sessions tab can stage a file while a session is open,
                  // and the staged card must not be invisible behind the
                  // player. Cancelling (X) returns to whatever was below.
                  renderUploadArea()
                ) : loadingJob && jobId ? (
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
                ) : jobId && job && (isStemsReady || isStage2 || isMixing || (!isRunning && !isStage1)) && videoUrl ? (
                  // Stems ready, Stage 2, or completed — show video player.
                  // No fixed aspect-ratio wrapper here: VideoPlayer sizes itself
                  // to the clip's real dimensions (landscape or portrait/Reel),
                  // bounded by the available pane space via max-w/max-h.
                  <VideoPlayer videoUrl={videoUrl} segments={displaySegs} speakers={spks} className="max-w-full max-h-full" jobId={jobId} projectId={projectId} jobStatus={job.status} overlays={jobOverlays} />
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

      {/* Destination picker — the fallback when there's no native Save As
          dialog (browser dev, or the desktop dialog failed). The path is on
          the machine running the backend, which is always this machine. */}
      <Modal
        open={!!exportPrompt}
        onClose={() => setExportPrompt(null)}
        title="Choose where to save"
        description={
          isDesktop()
            ? `The system save dialog couldn't be opened (${exportPrompt?.reason}). Type a destination instead.`
            : 'Type where the finished video should be written on this machine.'
        }
        size="md"
      >
        <div className="space-y-4">
          <InputField
            label="Destination"
            required
            value={exportPrompt?.path ?? ''}
            onChange={(e) => setExportPrompt((p) => (p ? { ...p, path: e.target.value } : p))}
            onKeyDown={(e) => { if (e.key === 'Enter') confirmExportPath() }}
            placeholder="~/Downloads/dubbed-video.mp4"
            autoFocus
          />
          <p className="text-[11px] leading-relaxed" style={{ color: 'var(--color-text-muted)' }}>
            A full file path or an existing folder both work — <code>~</code> is expanded for you,
            and any missing parent folders are created.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setExportPrompt(null)}>Cancel</Button>
            <Button onClick={confirmExportPath} disabled={!exportPrompt?.path.trim()} icon={<Download size={13} />}>
              Export
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}
