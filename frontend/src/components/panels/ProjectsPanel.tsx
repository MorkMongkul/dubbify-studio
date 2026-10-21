// src/components/panels/ProjectsPanel.tsx
// Projects tab of the dock — browse, create, open, and delete projects
// without leaving the editor. Replaces the old standalone /projects grid page.
import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderOpen, Plus, Search, Trash2, Check, X, Clapperboard, ChevronRight, ChevronDown, AlertCircle, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { useProjects, useDeleteProject } from '@/hooks/useApi'
import { CreateProjectModal } from '@/features/projects/CreateProjectModal'
import { getLanguageName } from '@/lib/utils'
import { PanelHeader, PanelEmptyState } from './PanelShell'
import { ProjectEpisodeList } from './ProjectEpisodeList'

interface ProjectsPanelProps {
  activeProjectId?: string
  /** Currently-open episode, if any — highlighted within its project's
   *  expanded episode list. */
  activeJobId?: string
  /** Import only ever targets the currently-open project — passed through
   *  to that project's expanded episode list only. */
  onImportFile?: (files: File[]) => void
}

export function ProjectsPanel({ activeProjectId, activeJobId, onImportFile }: ProjectsPanelProps) {
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

  // Which projects show their episode list inline. The active project
  // starts expanded automatically (you're clearly working in it — showing
  // its episodes needs no extra click, and it's derived rather than synced
  // via an effect so it can't fall out of sync with a changing route);
  // anything else opens on demand via the row's chevron, without navigating
  // away from wherever you are.
  const [manuallyExpandedIds, setManuallyExpandedIds] = useState<Set<string>>(new Set())
  const isExpanded = (id: string) => id === activeProjectId || manuallyExpandedIds.has(id)
  const toggleExpanded = (id: string) => {
    setManuallyExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

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
            className="flex items-center gap-1 text-[10px] font-semibold text-brand-400 hover:text-brand-300 hover:bg-brand-500/10 px-1.5 py-0.5 rounded transition-colors"
          >
            <Plus size={12} />
            <span>New</span>
          </button>
        }
      />
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Search */}
        <div className="relative flex items-center">
          <Search size={11} className="absolute left-2 text-ink-600 pointer-events-none" />
          <input
            className="w-full h-7 pl-6.5 pr-2 rounded border bg-ink-950/60 border-ink-800 text-[11px] text-ink-200 placeholder-ink-600 focus:outline-none focus:border-brand-500/50"
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
            <p className="text-[10.5px] font-semibold text-ink-300">Can't connect</p>
            <p className="text-[9px] text-ink-600 mt-0.5">Your projects are safe</p>
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="mt-2.5 flex items-center gap-1 text-[10px] font-semibold text-brand-400 hover:text-brand-300 px-2 py-1 rounded hover:bg-brand-500/10 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={10} className={isFetching ? 'animate-spin' : ''} />
              {isFetching ? 'Retrying…' : 'Retry'}
            </button>
          </div>
        ) : isLoading ? (
          <div className="space-y-1.5">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-12 rounded bg-ink-800/40 animate-pulse" style={{ animationDelay: `${i * 90}ms` }} />
            ))}
          </div>
        ) : projects.length === 0 ? (
          search ? (
            <div className="flex flex-col items-center justify-center py-6 text-center">
              <p className="text-[10px] text-ink-600">No projects match “{search}”</p>
              <button onClick={() => setSearch('')} className="mt-1.5 text-[10px] text-brand-400 hover:text-brand-300">
                Clear filter
              </button>
            </div>
          ) : (
            <PanelEmptyState
              icon={Clapperboard}
              title="No projects yet"
              actionLabel="Create your first project"
              onAction={() => setCreateOpen(true)}
            />
          )
        ) : (
          <div className="space-y-1">
            {projects.map((p) => {
              const isActive = p.id === activeProjectId
              const confirming = confirmDeleteId === p.id
              const expanded = isExpanded(p.id)
              const createdAt = new Date(p.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
              return (
                <div key={p.id}>
                  <div
                    onClick={() => { if (!isActive) navigate(`/projects/${p.id}`) }}
                    className={`group flex items-center gap-1.5 p-2 rounded cursor-pointer transition-all border ${isActive ? 'bg-brand-500/10 border-brand-500/30' : 'bg-ink-800/30 border-ink-800/40 hover:bg-ink-800/60 hover:border-ink-700/60'}`}
                  >
                    <button
                      onClick={(e) => { e.stopPropagation(); toggleExpanded(p.id) }}
                      className="shrink-0 p-0.5 rounded text-ink-500 hover:text-ink-200 hover:bg-white/10 transition-colors"
                      aria-label={expanded ? 'Collapse episodes' : 'Expand episodes'}
                    >
                      {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </button>
                    <div className="flex-1 min-w-0">
                      <p className={`text-[10.5px] font-medium truncate ${isActive ? 'text-white' : 'text-ink-300'}`}>{p.name}</p>
                      <p className="text-[9px] text-ink-600 flex items-center gap-0.5">
                        {getLanguageName(p.source_lang)}
                        <ChevronRight size={8} className="opacity-60" />
                        <span className="text-brand-400/70">{getLanguageName(p.target_lang)}</span>
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
                          className="h-6 w-6 rounded flex items-center justify-center text-ink-400 hover:bg-white/10"
                          aria-label="Cancel delete"
                        >
                          <X size={11} />
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(p.id) }}
                        className={`shrink-0 p-1 rounded transition-all ${isActive ? 'opacity-60 hover:opacity-100 hover:bg-red-500/20 hover:text-red-400 text-ink-400' : 'opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-red-500/20 hover:text-red-400 text-ink-500'}`}
                        title="Delete project"
                      >
                        <Trash2 size={11} />
                      </button>
                    )}
                  </div>
                  {expanded && (
                    <ProjectEpisodeList
                      projectId={p.id}
                      activeJobId={isActive ? activeJobId : undefined}
                      onImportFile={isActive ? onImportFile : undefined}
                    />
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
