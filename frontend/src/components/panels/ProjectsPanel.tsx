// src/components/panels/ProjectsPanel.tsx
// Projects tab of the dock — browse, create, open, and delete projects
// without leaving the editor. Replaces the old standalone /projects grid page.
import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderOpen, Plus, Search, Trash2, Check, X, Clapperboard, ChevronRight, AlertCircle, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { useProjects, useDeleteProject } from '@/hooks/useApi'
import { CreateProjectModal } from '@/features/projects/CreateProjectModal'
import { getLanguageName } from '@/lib/utils'
import { PanelHeader, PanelEmptyState } from './PanelShell'

interface ProjectsPanelProps {
  activeProjectId?: string
}

export function ProjectsPanel({ activeProjectId }: ProjectsPanelProps) {
  const navigate = useNavigate()
  const { data, isLoading, isError, refetch, isFetching } = useProjects()
  // Belt and braces: the API layer already rejects non-JSON, but a list panel
  // must never be one bad payload away from taking the whole editor down.
  // Memoised so the fallback isn't a fresh array identity on every render.
  const allProjects = useMemo(() => (Array.isArray(data) ? data : []), [data])
  const { mutate: deleteProject, isPending: deleting } = useDeleteProject()
  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  // A pending delete reverts on its own after a few seconds — no modal to
  // dismiss, no way to leave a row stuck in the scary state.
  useEffect(() => {
    if (!confirmDeleteId) return
    const t = setTimeout(() => setConfirmDeleteId(null), 4000)
    return () => clearTimeout(t)
  }, [confirmDeleteId])

  const projects = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return allProjects
    return allProjects.filter((p) => p.name.toLowerCase().includes(q))
  }, [allProjects, search])

  const handleDelete = (id: string) => {
    setConfirmDeleteId(null)
    deleteProject(id, {
      onSuccess: () => {
        toast.success('Project deleted')
        // Deleting the project that's open pulls the floor out from under the
        // editor — return to the bare workspace.
        if (id === activeProjectId) navigate('/', { replace: true })
      },
      onError: () => toast.error('Failed to delete project'),
    })
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader
        icon={FolderOpen}
        title="Projects"
        count={allProjects.length}
        action={
          <button
            onClick={() => setCreateOpen(true)}
            className="flex items-center gap-1 text-[10px] font-semibold text-purple-400 hover:text-purple-300 hover:bg-purple-500/10 px-1.5 py-0.5 rounded transition-colors"
          >
            <Plus size={12} />
            <span>New</span>
          </button>
        }
      />
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Search */}
        <div className="relative flex items-center">
          <Search size={11} className="absolute left-2 text-zinc-600 pointer-events-none" />
          <input
            className="w-full h-7 pl-6.5 pr-2 rounded border bg-zinc-950/60 border-zinc-800 text-[11px] text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-purple-500/50"
            style={{ paddingLeft: 26 }}
            placeholder="Filter projects…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {isError ? (
          // Distinct from "no projects" on purpose — an unreachable backend
          // used to render as an empty library, which reads as data loss.
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <AlertCircle size={18} className="text-amber-400 mb-2" />
            <p className="text-[10.5px] font-semibold text-zinc-300">Can't reach the backend</p>
            <p className="text-[9px] text-zinc-600 mt-0.5 max-w-[200px] leading-normal">
              Your projects are safe on the server — this is a connection problem, not missing data.
            </p>
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="mt-2.5 flex items-center gap-1 text-[10px] font-semibold text-purple-400 hover:text-purple-300 px-2 py-1 rounded hover:bg-purple-500/10 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={10} className={isFetching ? 'animate-spin' : ''} />
              {isFetching ? 'Retrying…' : 'Retry'}
            </button>
          </div>
        ) : isLoading ? (
          <div className="space-y-1.5">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-12 rounded bg-zinc-800/40 animate-pulse" style={{ animationDelay: `${i * 90}ms` }} />
            ))}
          </div>
        ) : projects.length === 0 ? (
          search ? (
            <div className="flex flex-col items-center justify-center py-6 text-center">
              <p className="text-[10px] text-zinc-600">No projects match “{search}”</p>
              <button onClick={() => setSearch('')} className="mt-1.5 text-[10px] text-purple-400 hover:text-purple-300">
                Clear filter
              </button>
            </div>
          ) : (
            <PanelEmptyState
              icon={Clapperboard}
              title="No projects yet"
              hint="A project groups a film's sessions, speakers, and logo."
              actionLabel="Create your first project"
              onAction={() => setCreateOpen(true)}
            />
          )
        ) : (
          <div className="space-y-1">
            {projects.map((p) => {
              const isActive = p.id === activeProjectId
              const confirming = confirmDeleteId === p.id
              const createdAt = new Date(p.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
              return (
                <div
                  key={p.id}
                  onClick={() => { if (!isActive) navigate(`/projects/${p.id}`) }}
                  className={`group flex items-center gap-2 p-2 rounded cursor-pointer transition-all border ${isActive ? 'bg-purple-500/10 border-purple-500/30' : 'bg-zinc-800/30 border-zinc-800/40 hover:bg-zinc-800/60 hover:border-zinc-700/60'}`}
                >
                  <div className="flex-1 min-w-0">
                    <p className={`text-[10.5px] font-medium truncate ${isActive ? 'text-white' : 'text-zinc-300'}`}>{p.name}</p>
                    <p className="text-[9px] text-zinc-600 flex items-center gap-0.5">
                      {getLanguageName(p.source_lang)}
                      <ChevronRight size={8} className="opacity-60" />
                      <span className="text-purple-400/70">{getLanguageName(p.target_lang)}</span>
                      <span className="mx-0.5 opacity-50">·</span>
                      {createdAt}
                    </p>
                  </div>
                  {confirming ? (
                    <div className="flex items-center gap-0.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                      <button
                        onClick={() => handleDelete(p.id)}
                        disabled={deleting}
                        className="flex items-center gap-0.5 px-1.5 h-6 rounded bg-red-500 hover:bg-red-400 text-white text-[9px] font-semibold transition-colors"
                      >
                        <Check size={10} /> Delete
                      </button>
                      <button
                        onClick={() => setConfirmDeleteId(null)}
                        className="h-6 w-6 rounded flex items-center justify-center text-zinc-400 hover:bg-white/10"
                        aria-label="Cancel delete"
                      >
                        <X size={11} />
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(p.id) }}
                      className={`shrink-0 p-1 rounded transition-all ${isActive ? 'opacity-60 hover:opacity-100 hover:bg-red-500/20 hover:text-red-400 text-zinc-400' : 'opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-red-500/20 hover:text-red-400 text-zinc-500'}`}
                      title="Delete project"
                    >
                      <Trash2 size={11} />
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      <CreateProjectModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}
