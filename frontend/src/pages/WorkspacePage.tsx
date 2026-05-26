// src/pages/WorkspacePage.tsx
// The unified Studio Workspace page - handles file upload, processing, video preview, timeline, and export.

import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { useDropzone } from 'react-dropzone'
import {
  ChevronLeft, ChevronRight, Mic2, Download, 
  Loader2, AlertCircle, Zap,
  PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen,
  Music, UploadCloud, Film, FileText, X,
  Activity, Users, VideoIcon, Clock
} from 'lucide-react'
import { toast } from 'sonner'

import {
  useJob, useSegments, useSpeakers, useSynthesizeJob, useMixFinalAudio,
  useProjects, useCreateProject, useUploadVideo, useUploadWithSubtitle
} from '@/hooks/useApi'
import { useEditorStore } from '@/store/editorStore'

import { VideoPlayer } from '@/components/video/VideoPlayer'
import { TimelineEditor } from '@/components/timeline/TimelineEditor'
import { TranscriptPanel } from '@/components/transcript/TranscriptPanel'
import { SpeakerPanel } from '@/components/speakers/SpeakerPanel'
import { Button } from '@/components/ui/Button'
import { PipelineStepper } from '@/features/upload/PipelineStepper'
import { LANGUAGE_OPTIONS } from '@/types'
import { getLanguageName, getJobStatusConfig, isJobRunning, cn } from '@/lib/utils'

// Placeholders for sidebar views when editor is empty
function SpeakerPanelPlaceholder() {
  return (
    <div className="h-full flex flex-col items-center justify-center p-5 text-center bg-neutral-bg2 select-none">
      <div className="h-10 w-10 rounded-xl bg-neutral-bg3 border border-border flex items-center justify-center text-white/30 mb-3">
        <Users size={16} />
      </div>
      <p className="text-xs font-semibold text-white/80 mb-1">No Speakers Detected</p>
      <p className="text-[10px] text-text-muted max-w-[160px] leading-normal">
        Upload a video file to automatically detect and clone speaker voices.
      </p>
    </div>
  )
}

function TranscriptPanelPlaceholder() {
  return (
    <div className="h-full flex flex-col items-center justify-center p-6 text-center bg-neutral-bg2 select-none border-l border-border">
      <div className="h-10 w-10 rounded-xl bg-neutral-bg3 border border-border flex items-center justify-center text-white/30 mb-3">
        <FileText size={16} />
      </div>
      <p className="text-xs font-semibold text-white/80 mb-1">No Script Transcript</p>
      <p className="text-[10px] text-text-muted max-w-[190px] leading-normal">
        Automated transcription and dialogue translations will appear here once processed.
      </p>
    </div>
  )
}

export default function WorkspacePage() {
  const { projectId, jobId } = useParams<{ projectId: string; jobId: string }>()
  const navigate = useNavigate()

  // --- Store States ---
  const {
    duration, leftPanelCollapsed, rightPanelCollapsed,
    toggleLeftPanel, toggleRightPanel, resetEditor,
  } = useEditorStore()

  // Reset editor state on jobId change
  useEffect(() => {
    if (jobId) {
      resetEditor()
    }
  }, [jobId])

  // --- API Query States (Active Session) ---
  const { data: job, isLoading: loadingJob } = useJob(jobId ?? null)
  const { data: segs = [], isLoading: loadingSegs } = useSegments(jobId ?? null)
  const { data: spks = [] } = useSpeakers(projectId ?? null)

  const { mutate: synthesize, isPending: synthesizing } = useSynthesizeJob()
  const { mutate: mix, isPending: mixing } = useMixFinalAudio()

  const isRunning = job ? isJobRunning(job.status) : false
  const statusConfig = job ? getJobStatusConfig(job.status) : null

  // --- Upload / Setup States ---
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [subtitleFile, setSubtitleFile] = useState<File | null>(null)
  const [sessionName, setSessionName] = useState('')
  const [sourceLang, setSourceLang] = useState('zh')
  const [targetLang, setTargetLang] = useState('kh')
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'upload' | 'recent'>('upload')

  const { data: allProjects, isLoading: loadingProjects } = useProjects()
  const { mutateAsync: createProject, isPending: creatingProject } = useCreateProject()
  const { mutateAsync: uploadVideo, isPending: uploadingVideo } = useUploadVideo()
  const { mutateAsync: uploadWithSub, isPending: uploadingWithSub } = useUploadWithSubtitle()
  
  const isUploading = uploadingVideo || uploadingWithSub
  const isSetupLoading = creatingProject || isUploading

  // --- Handlers ---
  const handleSynthesize = () => {
    if (!jobId) return
    synthesize(jobId, {
      onSuccess: () => toast.success('TTS synthesis started!'),
      onError: () => toast.error('Failed to start synthesis'),
    })
  }

  const handleMix = () => {
    if (!jobId) return
    mix(jobId, {
      onSuccess: () => toast.success('Audio mix started!'),
      onError: () => toast.error('Failed to start mix'),
    })
  }

  const handleExport = () => {
    if (!job?.output_url) {
      toast.error('No dubbed video available yet. Run Mix first.')
      return
    }
    window.open(job.output_url, '_blank')
  }

  // Active session status flags
  const hasApprovedSegs = segs.some((s) => s.is_approved)
  const hasTtsAudio = segs.some((s) => s.tts_audio_path !== '')
  const jobReady = job?.status === 'completed' && segs.length > 0
  const canMix = hasTtsAudio
  const canExport = !!job?.output_url
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
    setActiveTab('upload') // default back to upload panel
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

  const handleStartPipeline = async () => {
    if (!videoFile) {
      toast.error('Please select a video file first')
      return
    }

    setUploadError(null)
    setUploadProgress(0)

    try {
      const name = sessionName.trim() || `Dub: ${videoFile.name}`
      const project = await createProject({
        name,
        source_lang: sourceLang,
        target_lang: targetLang
      })

      const interval = setInterval(() => {
        setUploadProgress((p) => {
          if (p >= 92) {
            clearInterval(interval)
            return p
          }
          return p + Math.random() * 8
        })
      }, 300)

      let res
      if (subtitleFile) {
        res = await uploadWithSub({
          projectId: project.id,
          video: videoFile,
          subtitle: subtitleFile
        })
      } else {
        res = await uploadVideo({
          projectId: project.id,
          file: videoFile
        })
      }

      clearInterval(interval)
      setUploadProgress(100)
      toast.success('Upload complete! Starting AI pipeline.')
      
      setVideoFile(null)
      setSubtitleFile(null)
      setSessionName('')

      navigate(`/projects/${project.id}/jobs/${res.job_id}`)
    } catch (err: any) {
      setUploadProgress(0)
      const detail = err?.message || 'Failed to start pipeline'
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

            {/* Language selects and name */}
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
                ? `Dubbing... (${Math.round(uploadProgress)}%)` 
                : 'Start AI Dubbing Pipeline'
              }
            </Button>
          </div>
        </div>
      )
    }

    // Default Tabbed state: Upload dropzone or Recent list
    return (
      <div className="flex flex-col h-full w-full">
        {/* Tab Headers */}
        <div className="flex border-b border-border bg-neutral-bg3 shrink-0">
          <button
            onClick={() => setActiveTab('upload')}
            className={cn(
              "flex-1 py-2 text-xs font-semibold border-b-2 transition-all",
              activeTab === 'upload' ? "border-brand text-brand-300 bg-white/3" : "border-transparent text-text-muted hover:text-white"
            )}
          >
            Import Media
          </button>
          <button
            onClick={() => setActiveTab('recent')}
            className={cn(
              "flex-1 py-2 text-xs font-semibold border-b-2 transition-all",
              activeTab === 'recent' ? "border-brand text-brand-300 bg-white/3" : "border-transparent text-text-muted hover:text-white"
            )}
          >
            Recent Sessions
          </button>
        </div>

        {/* Tab Content */}
        <div className="flex-1 overflow-hidden min-h-0 relative">
          <AnimatePresence mode="wait">
            {activeTab === 'upload' ? (
              <motion.div
                key="upload-tab"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="h-full flex flex-col items-center justify-center p-6 text-center select-none"
              >
                <div className="h-12 w-12 rounded-2xl bg-neutral-bg3 border border-border flex items-center justify-center mb-3.5 text-brand-300 shadow-glow-sm">
                  <UploadCloud size={20} />
                </div>
                <p className="text-xs font-bold text-white mb-0.5">Drag & drop video here to import</p>
                <p className="text-[10px] text-text-muted mb-4 max-w-[200px] leading-normal">
                  Drop a file directly into the video player or timeline to load
                </p>
                
                {/* Manual file select */}
                <label className="inline-flex items-center justify-center h-8 px-3 rounded-lg border border-brand/20 bg-brand/5 hover:bg-brand/10 text-xs font-medium text-brand-300 cursor-pointer select-none transition-colors">
                  <input
                    type="file"
                    className="hidden"
                    accept="video/mp4,video/mkv,video/webm,video/avi,video/mov"
                    onChange={(e) => {
                      const files = Array.from(e.target.files || [])
                      if (files.length) onDropVideo(files)
                    }}
                  />
                  <span>Select Video File</span>
                </label>
              </motion.div>
            ) : (
              <motion.div
                key="recent-tab"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="h-full flex flex-col p-3 overflow-y-auto"
              >
                {loadingProjects && (
                  <div className="flex items-center justify-center h-full text-text-muted text-xs gap-2">
                    <Loader2 size={12} className="animate-spin" /> Loading...
                  </div>
                )}
                
                {!loadingProjects && allProjects?.length === 0 && (
                  <div className="flex flex-col items-center justify-center h-full text-center text-text-muted p-4">
                    <VideoIcon size={20} className="opacity-20 mb-1" />
                    <p className="text-[10px]">No recent sessions found.</p>
                  </div>
                )}

                {allProjects && (
                  <div className="space-y-1.5">
                    {allProjects.map((p) => (
                      <div
                        key={p.id}
                        onClick={() => navigate(`/projects/${p.id}`)}
                        className="group flex items-center justify-between p-2 rounded border border-border bg-neutral-bg3 hover:border-brand/40 cursor-pointer text-left transition-all"
                      >
                        <div className="min-w-0 pr-2">
                          <p className="text-[11px] font-semibold text-white truncate leading-snug group-hover:text-brand-300">
                            {p.name}
                          </p>
                          <div className="flex items-center gap-1.5 mt-0.5 text-[9px] text-text-muted">
                            <Clock size={8} />
                            <span>{new Date(p.created_at).toLocaleDateString()}</span>
                            <span>•</span>
                            <span>{getLanguageName(p.source_lang)} → {getLanguageName(p.target_lang)}</span>
                          </div>
                        </div>
                        <ChevronRight size={12} className="text-text-muted group-hover:text-brand-300" />
                      </div>
                    ))}
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    )
  }

  // Renders the processing view inside the center video player frame
  const renderPipelinePlayerArea = (activeJob: any) => {
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
          <div className="flex-1 flex items-center justify-center">
            {isRunning && (
              <div className="flex items-center gap-1.5 text-[11px] text-brand-300">
                <Loader2 size={11} className="animate-spin" />
                <span>{statusConfig?.description ?? 'Processing...'}</span>
              </div>
            )}
          </div>

          {/* Right Side: Panel Controls + Actions + Export */}
          <div className="flex items-center gap-1.5">
            {jobId && (
              <>
                {/* Panel triggers */}
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
                  icon={<Mic2 size={11} />}
                  title={!hasApprovedSegs ? 'Approve segments first' : undefined}
                >
                  Synthesize
                </Button>

                {/* Mix */}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleMix}
                  loading={mixing}
                  disabled={!canMix || mixing}
                  icon={<Music size={11} />}
                  title={!canMix ? 'Run Synthesize first' : undefined}
                >
                  Mix
                </Button>
              </>
            )}

            <div className="w-px h-4 mx-0.5" style={{ background: 'var(--color-border)' }} />

            {/* Export Button (Header Top Right) */}
            <Button
              variant={canExport ? 'default' : 'ghost'}
              size="sm"
              onClick={handleExport}
              disabled={!canExport}
              icon={<Download size={11} />}
              className={cn("shadow-glow transition-all", canExport ? "bg-brand text-white hover:bg-brand-hover" : "")}
            >
              Export
            </Button>
          </div>
        </header>

        {/* ─── Main Editor Grid Layout ──────────────────────────────── */}
        <div 
          className="flex-1 flex min-h-0 overflow-hidden w-full"
          onClick={(e) => e.stopPropagation()} // ignore clicks on background panels
        >
          
          {/* Left Sidebar: Speakers Panel or Placeholder */}
          <AnimatePresence initial={false}>
            {!leftPanelCollapsed && (
              <motion.div
                className="h-full border-r border-border overflow-hidden shrink-0 bg-neutral-bg2"
                initial={{ width: 0, opacity: 0 }}
                animate={{ width: 220, opacity: 1 }}
                exit={{ width: 0, opacity: 0 }}
                transition={{ type: 'spring', stiffness: 400, damping: 40 }}
              >
                {jobId && !isRunning && job?.status !== 'failed' ? (
                  <SpeakerPanel
                    speakers={spks}
                    projectId={projectId!}
                    className="h-full"
                  />
                ) : (
                  <SpeakerPanelPlaceholder />
                )}
              </motion.div>
            )}
          </AnimatePresence>

          {/* Center Area: Player (or Dropzone/Stepper) + Timeline (or Placeholder) */}
          <div className="flex-1 flex flex-col min-w-0 overflow-hidden bg-neutral-bg1">
            
            {/* Center Top: Player Grid Element */}
            <div className="flex-1 min-h-0 flex items-center justify-center p-4 bg-black/25 relative overflow-hidden">
              <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(ellipse_at_center,transparent_60%,rgba(0,0,0,0.3)_100%)]" />
              
              {/* Conditional inside Player box */}
              {loadingJob ? (
                // 1. Loading job indicator
                <div className="flex items-center gap-3 text-text-muted">
                  <Loader2 size={18} className="animate-spin text-brand" />
                  <span className="text-xs">Loading session data...</span>
                </div>
              ) : jobId && job?.status === 'failed' ? (
                // 2. Failure message
                <div className="flex flex-col items-center gap-3 text-center max-w-sm">
                  <div className="h-12 w-12 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400">
                    <AlertCircle size={20} />
                  </div>
                  <h4 className="text-xs font-bold text-white">Pipeline Execution Failed</h4>
                  <p className="text-[10px] text-text-muted leading-normal">
                    Error detail: {job.error_msg || 'Unknown pipeline failure.'}
                  </p>
                  <Button variant="outline" size="xs" onClick={() => navigate('/')} icon={<ChevronLeft size={11} />}>
                    Close Session
                  </Button>
                </div>
              ) : jobId && job && isRunning ? (
                // 3. Pipeline Stepper (actively processing)
                renderPipelinePlayerArea(job)
              ) : jobId && job && !isRunning ? (
                // 4. Completed Video Player
                <VideoPlayer
                  videoUrl={videoUrl}
                  segments={segs}
                  speakers={spks}
                  className="max-h-full max-w-full aspect-video w-full"
                />
              ) : (
                // 5. Setup View (tabbed Upload Dropzone / Recent sessions)
                <div className="glass-card max-w-lg w-full h-[320px] shadow-glow-sm overflow-hidden flex flex-col bg-neutral-bg2/90 border border-white/5">
                  {renderSetupPlayerArea()}
                </div>
              )}
            </div>

            {/* Center Bottom: Timeline Grid Element */}
            {jobId && !loadingJob && job && !isRunning && job.status !== 'failed' ? (
              // 1. Active Editor Timeline
              <TimelineEditor
                segments={segs}
                speakers={spks}
                duration={duration || (segs.length > 0 ? Math.max(...segs.map((s) => s.end_time)) + 5 : 60)}
                className="shrink-0 h-48 border-t border-border bg-neutral-bg2"
              />
            ) : (
              // 2. Dashed placeholder timeline
              <div 
                className="shrink-0 h-48 border-t border-border bg-neutral-bg2/50 flex flex-col items-center justify-center text-center p-6 border-dashed"
                style={{ borderWidth: '2px 0 0 0' }}
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

          {/* Right Sidebar: Transcript Panel or Placeholder */}
          <AnimatePresence initial={false}>
            {!rightPanelCollapsed && (
              <motion.div
                className="h-full border-l border-border overflow-hidden shrink-0 bg-neutral-bg2"
                initial={{ width: 0, opacity: 0 }}
                animate={{ width: 360, opacity: 1 }}
                exit={{ width: 0, opacity: 0 }}
                transition={{ type: 'spring', stiffness: 400, damping: 40 }}
              >
                {jobId && !isRunning && job?.status !== 'failed' ? (
                  <TranscriptPanel
                    segments={segs}
                    speakers={spks}
                    jobId={jobId!}
                    isLoading={loadingSegs}
                    className="h-full"
                  />
                ) : (
                  <TranscriptPanelPlaceholder />
                )}
              </motion.div>
            )}
          </AnimatePresence>

        </div>
      </div>
    </div>
  )
}
