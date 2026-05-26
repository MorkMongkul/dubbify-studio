// src/pages/ProjectsPage.tsx
import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, Search, Clapperboard, RefreshCw, AlertCircle, LayoutGrid } from 'lucide-react'
import { useProjects } from '@/hooks/useApi'
import { ProjectCard } from '@/features/projects/ProjectCard'
import { CreateProjectModal } from '@/features/projects/CreateProjectModal'
import { ProjectCardSkeleton } from '@/components/ui/Skeleton'
import { Button } from '@/components/ui/Button'
import { PageTransition } from '@/components/layout/PageTransition'

export default function ProjectsPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const [search, setSearch] = useState('')
  const { data: allProjects, isLoading, isError, refetch } = useProjects()

  const projects = useMemo(() => {
    if (!allProjects) return []
    if (!search.trim()) return allProjects
    const q = search.toLowerCase()
    return allProjects.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.source_lang ?? '').toLowerCase().includes(q) ||
        (p.target_lang ?? '').toLowerCase().includes(q)
    )
  }, [allProjects, search])

  return (
    <PageTransition>
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">

        {/* ── Toolbar bar ── */}
        <div
          className="tool-toolbar shrink-0"
          style={{ background: 'var(--color-surface-1)', borderColor: 'var(--color-border)' }}
        >
          {/* Left — page title */}
          <div className="flex items-center gap-2 mr-4">
            <LayoutGrid size={13} className="text-brand-400" />
            <span className="text-[13px] font-semibold text-white">Projects</span>
            {allProjects && (
              <span
                className="text-[11px] px-1.5 py-0.5 rounded font-mono"
                style={{ background: 'var(--color-surface-3)', color: 'var(--color-text-muted)' }}
              >
                {projects.length}
              </span>
            )}
          </div>

          <div className="tool-sep" />

          {/* Search */}
          <div className="relative flex items-center">
            <Search size={12} className="absolute left-2.5 text-white/30 pointer-events-none" />
            <input
              className="h-7 pl-7 pr-3 text-[12px] rounded-md border outline-none"
              style={{
                background: 'var(--glass-input-bg)',
                borderColor: 'var(--color-border)',
                color: 'var(--color-text-primary)',
                width: 200,
              }}
              placeholder="Filter projects…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Actions */}
          <Button
            variant="default"
            size="sm"
            onClick={() => setCreateOpen(true)}
            icon={<Plus size={13} />}
          >
            New Project
          </Button>
        </div>

        {/* ── Content ── */}
        <div className="flex-1 overflow-y-auto p-5">

          {/* Error */}
          {isError && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex flex-col items-center justify-center py-24 text-center"
            >
              <div
                className="h-12 w-12 rounded-xl border flex items-center justify-center mb-4"
                style={{ background: 'rgba(239,68,68,0.08)', borderColor: 'rgba(239,68,68,0.2)' }}
              >
                <AlertCircle size={20} className="text-red-400" />
              </div>
              <p className="text-[13px] font-semibold text-white mb-1">Backend unreachable</p>
              <p className="text-[12px] text-white/40 mb-4">Check that the API is running at localhost:8000</p>
              <Button variant="outline" size="sm" onClick={() => refetch()} icon={<RefreshCw size={12} />}>
                Retry
              </Button>
            </motion.div>
          )}

          {/* Loading */}
          {isLoading && !isError && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
              {[...Array(12)].map((_, i) => <ProjectCardSkeleton key={i} />)}
            </div>
          )}

          {/* Empty */}
          {!isLoading && !isError && projects.length === 0 && (
            <AnimatePresence>
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex flex-col items-center justify-center py-28 text-center"
              >
                <div
                  className="h-16 w-16 rounded-2xl border flex items-center justify-center mb-5"
                  style={{ background: 'var(--color-surface-3)', borderColor: 'var(--color-border)' }}
                >
                  <Clapperboard size={26} className="text-white/20" />
                </div>
                <h3 className="text-[14px] font-semibold text-white mb-1.5">
                  {search ? 'No results' : 'No projects yet'}
                </h3>
                <p className="text-[12px] text-white/35 max-w-[240px] mb-5">
                  {search
                    ? `No projects match "${search}"`
                    : 'Create a project to start dubbing your video content with AI.'}
                </p>
                {!search && (
                  <Button variant="default" size="sm" onClick={() => setCreateOpen(true)} icon={<Plus size={13} />}>
                    Create Project
                  </Button>
                )}
              </motion.div>
            </AnimatePresence>
          )}

          {/* Grid */}
          {!isLoading && !isError && projects.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-3">
              {projects.map((project, i) => (
                <ProjectCard key={project.id} project={project} index={i} />
              ))}
            </div>
          )}
        </div>
      </div>

      <CreateProjectModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </PageTransition>
  )
}
