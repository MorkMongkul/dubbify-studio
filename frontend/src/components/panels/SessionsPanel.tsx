// src/components/panels/SessionsPanel.tsx
// Sessions tab of the dock — import a new video into the open project and
// switch between that project's dubbing sessions. Extracted from EditorPage's
// old no-session left panel; now reachable at any time via the icon rail.
import { useNavigate } from 'react-router-dom'
import {
  Film, VideoIcon, UploadCloud, Loader2, Trash2,
  CheckCircle2, AlertCircle, Scissors,
} from 'lucide-react'
import { toast } from 'sonner'
import { useProjectJobs, useDeleteJob } from '@/hooks/useApi'
import { PanelHeader, PanelEmptyState } from './PanelShell'

interface SessionsPanelProps {
  projectId?: string
  activeJobId?: string
  /** Staged-upload flow lives in EditorPage (the center pane renders the
   *  staged card) — the panel only hands the chosen file up. */
  onImportFile: (files: File[]) => void
  /** Empty-state CTA — flips the rail to the Projects tab. */
  onShowProjects: () => void
}

export function SessionsPanel({ projectId, activeJobId, onImportFile, onShowProjects }: SessionsPanelProps) {
  const navigate = useNavigate()
  const { data: projectJobs = [], isLoading: loadingJobs } = useProjectJobs(projectId ?? null)
  const { mutate: deleteJob, isPending: deletingJob } = useDeleteJob()

  const handleDeleteJob = (e: React.MouseEvent, jId: string) => {
    e.stopPropagation()
    if (!projectId) return
    deleteJob({ jobId: jId, projectId }, {
      onSuccess: () => {
        toast.success('Session deleted')
        if (jId === activeJobId) navigate(`/projects/${projectId}`, { replace: true })
      },
      onError: () => toast.error('Failed to delete session'),
    })
  }

  if (!projectId) {
    return (
      <div className="h-full flex flex-col min-h-0">
        <PanelHeader icon={Film} title="Sessions" />
        <PanelEmptyState
          icon={VideoIcon}
          title="No project open"
          hint="Sessions live inside a project — pick or create one first."
          actionLabel="Browse projects"
          onAction={onShowProjects}
        />
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader icon={Film} title="Sessions" count={projectJobs.length} />
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Import new video */}
        <label className="flex items-center gap-2 p-2 rounded border border-dashed border-zinc-700/60 hover:border-purple-500/50 hover:bg-purple-500/5 cursor-pointer transition-all group">
          <input
            type="file"
            className="hidden"
            accept="video/mp4,video/mkv,video/webm,video/avi,video/mov"
            onChange={(e) => {
              const files = Array.from(e.target.files || [])
              if (files.length) onImportFile(files)
              e.target.value = ''
            }}
          />
          <div className="h-6 w-6 rounded-md bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0">
            <UploadCloud size={11} className="text-purple-400" />
          </div>
          <span className="text-[11px] text-zinc-400 group-hover:text-zinc-200 transition-colors">Import new video</span>
        </label>

        {/* Session list */}
        {loadingJobs && (
          <div className="flex items-center gap-1.5 text-zinc-600 text-[10px] py-2">
            <Loader2 size={10} className="animate-spin" /> Loading…
          </div>
        )}
        {projectJobs.length > 0 && (
          <div className="space-y-1">
            {projectJobs.map(j => {
              const isActive = j.id === activeJobId
              const isDone = j.status === 'completed'
              const isFailed = j.status === 'failed'
              const isStemsReadyJob = j.status === 'stems_ready'
              const filename = j.video_path ? j.video_path.split('/').pop() : 'Video'
              const createdAt = new Date(j.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
              return (
                <div
                  key={j.id}
                  onClick={() => navigate(`/projects/${projectId}/jobs/${j.id}`)}
                  className={`group flex items-center gap-2 p-2 rounded cursor-pointer transition-all border ${isActive ? 'bg-purple-500/10 border-purple-500/30 text-white' : 'bg-zinc-800/30 border-zinc-800/40 hover:bg-zinc-800/60 hover:border-zinc-700/60'}`}
                >
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
                  <button
                    onClick={e => handleDeleteJob(e, j.id)}
                    disabled={deletingJob}
                    className={`shrink-0 p-1 rounded transition-all ${isActive ? 'opacity-60 hover:opacity-100 hover:bg-red-500/20 hover:text-red-400 text-zinc-400' : 'opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-red-500/20 hover:text-red-400 text-zinc-500'}`}
                  >
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
    </div>
  )
}
