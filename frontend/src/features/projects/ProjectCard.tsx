// src/features/projects/ProjectCard.tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Clapperboard, MoreHorizontal, Trash2, FolderOpen,
  ArrowRight, Clock, VideoIcon, ChevronRight
} from 'lucide-react'
import { toast } from 'sonner'
import type { Project } from '@/types'
import { getLanguageName } from '@/lib/utils'
import { useDeleteProject } from '@/hooks/useApi'

interface ProjectCardProps {
  project: Project
  index?: number
}

export function ProjectCard({ project, index = 0 }: ProjectCardProps) {
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const { mutate: deleteProject, isPending: deleting } = useDeleteProject()

  const srcLang = getLanguageName(project.source_language)
  const tgtLang = getLanguageName(project.target_language)
  const createdAt = new Date(project.created_at).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric'
  })

  const handleDelete = () => {
    deleteProject(project.id, {
      onSuccess: () => toast.success('Project deleted'),
      onError:   () => toast.error('Failed to delete project'),
    })
    setMenuOpen(false)
  }

  const handleOpen = () => navigate(`/projects/${project.id}`)

  return (
    <motion.div
      className="group relative rounded-lg border overflow-hidden cursor-pointer"
      style={{
        background: 'var(--color-surface-2)',
        borderColor: 'var(--color-border)',
      }}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.1, delay: index * 0.025 }}
      whileHover={{ borderColor: 'rgba(124,58,237,0.4)' }}
      whileTap={{ scale: 0.995 }}
      onClick={handleOpen}
    >
      {/* Thumbnail strip */}
      <div
        className="h-[88px] flex items-center justify-center relative overflow-hidden"
        style={{ background: 'var(--color-surface-3)' }}
      >
        {/* Grid pattern */}
        <div className="absolute inset-0 opacity-30"
          style={{
            backgroundImage: 'repeating-linear-gradient(0deg,transparent,transparent 19px,var(--color-border) 19px,var(--color-border) 20px),repeating-linear-gradient(90deg,transparent,transparent 19px,var(--color-border) 19px,var(--color-border) 20px)',
          }}
        />
        <div className="relative flex flex-col items-center gap-1.5">
          <VideoIcon size={22} className="text-white/20" />
          <div className="flex items-center gap-1">
            <span className="text-[10px] font-medium text-white/30">{srcLang}</span>
            <ChevronRight size={9} className="text-white/20" />
            <span className="text-[10px] font-medium text-brand-400/70">{tgtLang}</span>
          </div>
        </div>

        {/* Hover overlay */}
        <div className="absolute inset-0 bg-brand/0 group-hover:bg-brand/5 transition-colors duration-200" />

        {/* Menu button */}
        <div
          className="absolute top-2 right-2 z-10"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="h-6 w-6 rounded flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity text-white/60 hover:text-white hover:bg-white/10"
            onClick={() => setMenuOpen((v) => !v)}
          >
            <MoreHorizontal size={13} />
          </button>
          <AnimatePresence>
            {menuOpen && (
              <motion.div
                className="absolute right-0 top-full mt-1 w-36 rounded-lg border overflow-hidden z-20"
                style={{
                  background: 'var(--color-surface-4)',
                  borderColor: 'var(--color-border-strong)',
                }}
                initial={{ opacity: 0, scale: 0.94, y: -4 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.94, y: -4 }}
                transition={{ duration: 0.08 }}
              >
                <button
                  className="flex items-center gap-2 w-full px-3 py-2 text-xs text-white/60 hover:text-white hover:bg-white/5 transition-colors"
                  onClick={handleOpen}
                >
                  <FolderOpen size={12} /> Open
                </button>
                <div className="h-px mx-2" style={{ background: 'var(--color-border)' }} />
                <button
                  className="flex items-center gap-2 w-full px-3 py-2 text-xs text-red-400 hover:bg-red-500/10 transition-colors"
                  onClick={handleDelete}
                  disabled={deleting}
                >
                  <Trash2 size={12} />
                  {deleting ? 'Deleting…' : 'Delete'}
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Info row */}
      <div className="px-3 py-2.5 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-white truncate leading-tight">{project.name}</p>
          <div className="flex items-center gap-1 mt-0.5">
            <Clock size={9} className="text-white/25 shrink-0" />
            <span className="text-[10px] text-white/30">{createdAt}</span>
            <span className="text-[10px] text-white/20 mx-0.5">·</span>
            <span className="text-[10px] text-white/30">{project.job_count ?? 0} job{(project.job_count ?? 0) !== 1 ? 's' : ''}</span>
          </div>
        </div>
        <ArrowRight
          size={13}
          className="shrink-0 text-white/0 group-hover:text-brand-400 transition-colors duration-150"
        />
      </div>

      {menuOpen && (
        <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
      )}
    </motion.div>
  )
}
