// src/components/panels/ProjectEpisodeList.tsx
// The episode (session) list for one project, rendered inline under its row
// in ProjectsPanel — expanding a project reveals every episode right there
// instead of requiring a separate "Sessions" tab and an extra click.
import { useEffect, useState } from 'react'
import {
  UploadCloud, Loader2, Trash2,
  CheckCircle2, AlertCircle, Scissors, Check, X,
} from 'lucide-react'
import { toast } from 'sonner'
import { useNavigate } from 'react-router-dom'
import { useProjectJobs, useDeleteJob, useSetJobTitle } from '@/hooks/useApi'

interface ProjectEpisodeListProps {
  projectId: string
  /** Only set when this IS the currently-open project — episodes elsewhere
   *  never have an "active" row, since you're not inside them. */
  activeJobId?: string
  /** Import only ever targets the currently-open project (the staged-upload
   *  flow always uploads to whatever's in the URL) — omitted for any other
   *  project's expanded list. */
  onImportFile?: (files: File[]) => void
}

export function ProjectEpisodeList({ projectId, activeJobId, onImportFile }: ProjectEpisodeListProps) {
  const navigate = useNavigate()
  const { data: projectJobs = [], isLoading: loadingJobs } = useProjectJobs(projectId)
  const { mutate: deleteJob, isPending: deletingJob } = useDeleteJob()
  const setJobTitle = useSetJobTitle()
  const [editingJobId, setEditingJobId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')

  // Deleting an episode removes its media and every segment — same two-step
  // confirm as projects, reverting on its own after a few seconds.
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  useEffect(() => {
    if (!confirmDeleteId) return
    const t = setTimeout(() => setConfirmDeleteId(null), 4000)
    return () => clearTimeout(t)
  }, [confirmDeleteId])

  const handleDeleteJob = (jId: string) => {
    setConfirmDeleteId(null)
    deleteJob({ jobId: jId, projectId }, {
      onSuccess: () => {
        toast.success('Session deleted')
        // Don't leave the editor sitting on a session that no longer exists.
        if (jId === activeJobId) navigate(`/projects/${projectId}`, { replace: true })
      },
      onError: () => toast.error('Failed to delete session'),
    })
  }

  return (
    <div className="pl-4 pr-1 py-1 space-y-1 border-l border-ink-800/60 ml-3">
      {onImportFile && (
        <label className="flex items-center gap-2 p-1.5 rounded border border-dashed border-ink-700/60 hover:border-brand-500/50 hover:bg-brand-500/5 cursor-pointer transition-all group">
          <input
            type="file"
            className="hidden"
            accept="video/mp4,video/mkv,video/webm,video/avi,video/mov"
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => {
              const files = Array.from(e.target.files || [])
              if (files.length) onImportFile(files)
              e.target.value = ''
            }}
          />
          <div className="h-5 w-5 rounded-md bg-brand-500/10 border border-brand-500/20 flex items-center justify-center shrink-0">
            <UploadCloud size={10} className="text-brand-400" />
          </div>
          <span className="text-[10px] text-ink-400 group-hover:text-ink-200 transition-colors">Import new video</span>
        </label>
      )}

      {loadingJobs && (
        <div className="flex items-center gap-1.5 text-ink-600 text-[9px] py-1.5">
          <Loader2 size={9} className="animate-spin" /> Loading…
        </div>
      )}

      {!loadingJobs && projectJobs.length === 0 && (
        <p className="text-[9px] text-ink-700 py-1">
          {onImportFile ? 'No episodes yet — import a video above' : 'No episodes yet'}
        </p>
      )}

      {projectJobs.map((j) => {
        const isActive = j.id === activeJobId
        const isDone = j.status === 'completed'
        const isFailed = j.status === 'failed'
        const isStemsReadyJob = j.status === 'stems_ready'
        const filename = j.video_path ? j.video_path.split('/').pop() : 'Video'
        const label = j.title || filename
        const isEditing = editingJobId === j.id

        return (
          <div
            key={j.id}
            onClick={() => !isEditing && navigate(`/projects/${projectId}/jobs/${j.id}`)}
            className={`group flex items-center gap-1.5 p-1.5 rounded cursor-pointer transition-all border ${isActive ? 'bg-brand-500/10 border-brand-500/30 text-white' : 'bg-ink-800/20 border-ink-800/40 hover:bg-ink-800/50 hover:border-ink-700/60'}`}
          >
            <div className="shrink-0">
              {isDone ? <CheckCircle2 size={11} className="text-emerald-400" />
                : isFailed ? <AlertCircle size={11} className="text-red-400" />
                : isStemsReadyJob ? <Scissors size={11} className="text-emerald-400" />
                : <Loader2 size={11} className="text-brand-400 animate-spin" />}
            </div>
            <div className="flex-1 min-w-0">
              {isEditing ? (
                <input
                  autoFocus
                  className="w-full bg-ink-950 text-[9.5px] text-ink-200 rounded px-1 py-0.5 focus:outline-none border border-brand-500/50"
                  value={editingTitle}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  onBlur={() => {
                    const trimmed = editingTitle.trim()
                    if (trimmed && trimmed !== j.title) {
                      setJobTitle.mutate({ jobId: j.id, title: trimmed })
                    }
                    setEditingJobId(null)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') e.currentTarget.blur()
                    if (e.key === 'Escape') setEditingJobId(null)
                  }}
                />
              ) : (
                <p
                  className={`text-[9.5px] font-medium truncate ${isActive ? 'text-white' : 'text-ink-400'} hover:text-white`}
                  onClick={(e) => {
                    e.stopPropagation()
                    setEditingJobId(j.id)
                    setEditingTitle(j.title || filename || '')
                  }}
                  title="Click to rename"
                >
                  {label}
                </p>
              )}
            </div>
            {confirmDeleteId === j.id ? (
              <div className="flex items-center gap-0.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                <button
                  onClick={() => handleDeleteJob(j.id)}
                  disabled={deletingJob}
                  className="flex items-center gap-0.5 px-1.5 h-5 rounded bg-red-500 hover:bg-red-400 text-white text-[9px] font-semibold transition-colors"
                >
                  <Check size={9} /> Delete
                </button>
                <button
                  onClick={() => setConfirmDeleteId(null)}
                  className="h-5 w-5 rounded flex items-center justify-center text-ink-400 hover:bg-white/10"
                  aria-label="Cancel delete"
                >
                  <X size={10} />
                </button>
              </div>
            ) : (
              <button
                onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(j.id) }}
                disabled={deletingJob}
                className={`shrink-0 p-1 rounded transition-all ${isActive ? 'opacity-60 hover:opacity-100 hover:bg-red-500/20 hover:text-red-400 text-ink-400' : 'opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-red-500/20 hover:text-red-400 text-ink-500'}`}
                title="Delete session"
                aria-label="Delete session"
              >
                <Trash2 size={10} />
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
