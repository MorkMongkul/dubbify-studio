// src/features/upload/PipelineStepper.tsx
import { motion } from 'framer-motion'
import {
  Upload, AudioWaveform, UsersRound, FileText, Languages,
  CheckCircle2, Loader2, AlertCircle, Mic2, Music2
} from 'lucide-react'
import type { Job } from '@/types'
import { cn } from '@/lib/utils'

const STEPS = [
  { key: 'uploading',    label: 'Upload',      icon: Upload       },
  { key: 'extracting',   label: 'Extract',     icon: AudioWaveform },
  { key: 'diarizing',    label: 'Speakers',    icon: UsersRound   },
  { key: 'transcribing', label: 'Transcribe',  icon: FileText     },
  { key: 'translating',  label: 'Translate',   icon: Languages    },
  { key: 'ready',        label: 'Ready',       icon: CheckCircle2 },
]

const STATUS_ORDER = [
  'uploading', 'extracting', 'diarizing', 'transcribing',
  'translating', 'ready', 'synthesizing', 'mixing', 'done'
]

type StepState = 'done' | 'active' | 'error' | 'pending'

function getStepState(stepKey: string, jobStatus: string): StepState {
  if (jobStatus === 'error') return 'error'
  const jobIdx  = STATUS_ORDER.indexOf(jobStatus)
  const stepIdx = STATUS_ORDER.indexOf(stepKey)
  if (stepIdx < jobIdx)  return 'done'
  if (stepIdx === jobIdx) return 'active'
  return 'pending'
}

interface PipelineStepperProps {
  job: Job
  compact?: boolean
}

export function PipelineStepper({ job, compact = false }: PipelineStepperProps) {
  if (compact) return <PipelineStepperCompact job={job} />

  const isDone  = ['ready','synthesizing','mixing','done'].includes(job.status)
  const isError = job.status === 'error'

  return (
    <div
      className="rounded-lg border p-4"
      style={{ background: 'var(--color-surface-3)', borderColor: 'var(--color-border)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <span className="text-[11px] font-semibold text-white/50 uppercase tracking-wider">AI Pipeline</span>
        {isError && (
          <div className="flex items-center gap-1 text-[11px] text-red-400">
            <AlertCircle size={11} />
            <span>Failed</span>
          </div>
        )}
        {isDone && (
          <div className="flex items-center gap-1 text-[11px] text-emerald-400">
            <CheckCircle2 size={11} />
            <span>Complete</span>
          </div>
        )}
      </div>

      {/* Steps */}
      <div className="flex items-start">
        {STEPS.map((step, i) => {
          const state  = getStepState(step.key, job.status)
          const isLast = i === STEPS.length - 1
          const Icon   = step.icon

          return (
            <div key={step.key} className="flex items-start flex-1">
              {/* Step node */}
              <div className="flex flex-col items-center gap-1.5 flex-1">
                {/* Icon */}
                <motion.div
                  className={cn(
                    'h-8 w-8 rounded-lg border flex items-center justify-center transition-all duration-200',
                    state === 'done'    && 'border-emerald-500/30 text-emerald-400',
                    state === 'active'  && 'border-brand/40 text-brand-300',
                    state === 'error'   && 'border-red-500/30 text-red-400',
                    state === 'pending' && 'text-white/20',
                  )}
                  style={{
                    background: state === 'done'    ? 'rgba(16,185,129,0.08)'
                               : state === 'active'  ? 'rgba(124,58,237,0.12)'
                               : state === 'error'   ? 'rgba(239,68,68,0.08)'
                               : 'var(--color-surface-4)',
                    borderColor: state === 'done'    ? 'rgba(16,185,129,0.25)'
                                : state === 'active'  ? 'rgba(124,58,237,0.3)'
                                : state === 'error'   ? 'rgba(239,68,68,0.25)'
                                : 'var(--color-border)',
                  }}
                  animate={state === 'active' ? { opacity: [0.7, 1, 0.7] } : {}}
                  transition={{ duration: 1.8, repeat: Infinity }}
                >
                  {state === 'active'  ? <Loader2 size={14} className="animate-spin" />
                   : state === 'done'   ? <CheckCircle2 size={14} />
                   : state === 'error'  ? <AlertCircle size={14} />
                   : <Icon size={14} />}
                </motion.div>

                {/* Label */}
                <span className={cn(
                  'text-[10px] font-medium text-center',
                  state === 'done'    && 'text-emerald-400',
                  state === 'active'  && 'text-brand-300',
                  state === 'error'   && 'text-red-400',
                  state === 'pending' && 'text-white/25',
                )}>
                  {step.label}
                </span>
              </div>

              {/* Connector line */}
              {!isLast && (
                <div className="flex-1 h-px mt-4 relative overflow-hidden mx-0.5" style={{ background: 'var(--color-border)' }}>
                  {state === 'done' && (
                    <div className="absolute inset-0 bg-emerald-500/30" />
                  )}
                  {state === 'active' && (
                    <motion.div
                      className="absolute inset-y-0 w-10 bg-gradient-to-r from-transparent via-brand to-transparent"
                      animate={{ x: ['-100%', '200%'] }}
                      transition={{ duration: 1.2, repeat: Infinity, ease: 'easeInOut' }}
                    />
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Error details */}
      {isError && job.error_message && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-3 px-3 py-2 rounded-md border text-[11px] text-red-400"
          style={{ background: 'rgba(239,68,68,0.06)', borderColor: 'rgba(239,68,68,0.2)' }}
        >
          {job.error_message}
        </motion.div>
      )}
    </div>
  )
}

function PipelineStepperCompact({ job }: { job: Job }) {
  const currentIdx = STATUS_ORDER.indexOf(job.status)
  const progress = Math.max(0, Math.min(100, (currentIdx / (STATUS_ORDER.length - 1)) * 100))

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-white/40 capitalize">{job.status.replace('_', ' ')}</span>
        <span className="text-[11px] font-mono text-white/40">{Math.round(progress)}%</span>
      </div>
      <div className="h-1 rounded-full overflow-hidden" style={{ background: 'var(--color-surface-4)' }}>
        <motion.div
          className="h-full rounded-full bg-gradient-to-r from-brand to-accent"
          initial={{ width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.4 }}
        />
      </div>
    </div>
  )
}
