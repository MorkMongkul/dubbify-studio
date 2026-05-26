// src/pages/ProjectPage.tsx
import { useNavigate, useParams, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  ArrowLeft, ChevronRight, Clock,
  Loader2, AlertCircle, CheckCircle2, PenLine,
  UploadCloud, Activity, VideoIcon
} from 'lucide-react'
import { useProject, useProjectJobs } from '@/hooks/useApi'
import { UploadDropzone } from '@/features/upload/UploadDropzone'
import { PipelineStepper } from '@/features/upload/PipelineStepper'
import { PageTransition } from '@/components/layout/PageTransition'
import { Button } from '@/components/ui/Button'
import { StatusBadge } from '@/components/ui/Badge'
import { Skeleton } from '@/components/ui/Skeleton'
import { getLanguageName, getJobStatusConfig, isJobRunning } from '@/lib/utils'
import type { Job } from '@/types'

function JobCard({ job, projectId }: { job: Job; projectId: string }) {
  const navigate = useNavigate()
  const config  = getJobStatusConfig(job.status)
  const running = isJobRunning(job.status)
  const ready   = ['completed', 'synthesizing', 'mixing'].includes(job.status)
  const createdAt = new Date(job.created_at).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  })

  return (
    <motion.div
      className="rounded-lg border overflow-hidden"
      style={{ background: 'var(--color-surface-2)', borderColor: 'var(--color-border)' }}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.1 }}
    >
      {/* Job header */}
      <div
        className="flex items-center justify-between gap-3 px-4 py-2.5 border-b"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <VideoIcon size={12} className="text-white/35 shrink-0" />
          <span className="text-[12px] font-medium text-white truncate">
            {job.video_path ? job.video_path.split('/').pop() : 'Video Upload'}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <div className="flex items-center gap-1 text-[11px] text-white/30">
            <Clock size={10} />
            <span>{createdAt}</span>
          </div>
          <StatusBadge status={job.status} label={config.label} />
        </div>
      </div>

      {/* Pipeline */}
      <div className="p-4">
        <PipelineStepper job={job} />
      </div>

      {/* Footer */}
      <div
        className="flex items-center justify-between px-4 py-2 border-t"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}
      >
        {running ? (
          <div className="flex items-center gap-1.5 text-[11px] text-white/45">
            <Loader2 size={11} className="animate-spin text-brand-400" />
            <span>{config.description}</span>
          </div>
        ) : job.status === 'failed' ? (
          <div className="flex items-center gap-1.5 text-[11px] text-red-400">
            <AlertCircle size={11} />
            <span>Pipeline failed</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 text-[11px] text-emerald-400">
            <CheckCircle2 size={11} />
            <span>{config.description}</span>
          </div>
        )}

        {ready && (
          <Button
            variant="default"
            size="sm"
            onClick={() => navigate(`/projects/${projectId}/jobs/${job.id}`)}
            icon={<PenLine size={12} />}
          >
            Open Editor
          </Button>
        )}
      </div>
    </motion.div>
  )
}

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const { data: project, isLoading: loadingProject } = useProject(projectId ?? null)
  const { data: jobList, isLoading: loadingJobs, refetch: refetchJobs } = useProjectJobs(projectId ?? null)

  const srcLang = project ? getLanguageName(project.source_lang) : ''
  const tgtLang = project ? getLanguageName(project.target_lang) : ''
  // One project = one video: hide upload once any job exists
  const hasAnyJob = (jobList?.length ?? 0) > 0

  return (
    <PageTransition>
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">

        {/* ── Toolbar ── */}
        <div
          className="tool-toolbar"
          style={{ background: 'var(--color-surface-1)', borderColor: 'var(--color-border)' }}
        >
          {/* Back */}
          <Link to="/projects">
            <button className="tool-btn mr-1">
              <ArrowLeft size={14} />
            </button>
          </Link>

          <div className="tool-sep" />

          {/* Breadcrumb */}
          <div className="flex items-center gap-1.5 text-[12px]">
            <Link to="/projects" className="text-white/40 hover:text-white transition-colors">Projects</Link>
            <ChevronRight size={11} className="text-white/20" />
            {loadingProject
              ? <Skeleton className="h-3.5 w-24" />
              : <span className="text-white font-medium">{project?.name}</span>
            }
            {(srcLang && tgtLang) && (
              <>
                <span className="text-white/20 mx-1">·</span>
                <span className="text-white/40">{srcLang}</span>
                <ChevronRight size={10} className="text-white/20" />
                <span className="text-brand-400 font-medium">{tgtLang}</span>
              </>
            )}
          </div>

          <div className="flex-1" />

          {/* Job count */}
          {loadingJobs && <Loader2 size={12} className="animate-spin text-white/30" />}
          {jobList && (
            <span
              className="text-[11px] px-1.5 py-0.5 rounded font-mono"
              style={{ background: 'var(--color-surface-3)', color: 'var(--color-text-muted)' }}
            >
              {jobList.length} job{jobList.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* ── Body ── */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto px-6 py-5 space-y-5">

            {/* Upload section — only shown when no job exists yet */}
            {!hasAnyJob && !loadingJobs && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.1 }}
              >
                <div className="flex items-center gap-2 mb-2.5">
                  <UploadCloud size={13} className="text-white/40" />
                  <span className="text-[12px] font-semibold text-white/70 uppercase tracking-wider">Upload Video</span>
                </div>
                <UploadDropzone
                  projectId={projectId!}
                  onSuccess={() => refetchJobs()}
                />
              </motion.div>
            )}

            {/* Jobs section */}
            {(loadingJobs || (jobList && jobList.length > 0)) && (
              <div>
                <div className="flex items-center gap-2 mb-2.5">
                  <Activity size={13} className="text-white/40" />
                  <span className="text-[12px] font-semibold text-white/70 uppercase tracking-wider">Processing Jobs</span>
                  {loadingJobs && <Loader2 size={11} className="animate-spin text-white/25" />}
                </div>

                {loadingJobs && !jobList && (
                  <div className="space-y-3">
                    {[...Array(2)].map((_, i) => (
                      <div key={i} className="rounded-lg border p-4" style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-2)' }}>
                        <Skeleton className="h-4 w-48 mb-3" />
                        <Skeleton className="h-16 w-full" />
                      </div>
                    ))}
                  </div>
                )}

                {jobList && (
                  <div className="space-y-3">
                    {jobList.map((job) => (
                      <JobCard key={job.id} job={job} projectId={projectId!} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </PageTransition>
  )
}
