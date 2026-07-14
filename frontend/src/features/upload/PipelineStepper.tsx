// src/features/upload/PipelineStepper.tsx
import { motion } from 'framer-motion'
import {
  AudioWaveform, UsersRound, FileText, Languages,
  CheckCircle2, Loader2, AlertCircle, Mic2, Music2, Hourglass, Scissors
} from 'lucide-react'
import type { Job } from '@/types'
import { cn } from '@/lib/utils'

// Steps matching backend JobStatus enum order exactly
const STEPS = [
  { key: 'extracting',   label: 'Extract',    icon: AudioWaveform },
  { key: 'separating',   label: 'Separate',   icon: Scissors     },
  { key: 'diarizing',    label: 'Speakers',   icon: UsersRound   },
  { key: 'transcribing', label: 'Transcribe', icon: FileText     },
  { key: 'translating',  label: 'Translate',  icon: Languages    },
  { key: 'synthesizing', label: 'Synthesize', icon: Mic2         },
  { key: 'mixing',       label: 'Mix',        icon: Music2       },
]

// Full ordered status list matching backend JobStatus enum
const STATUS_ORDER = [
  'pending',
  'extracting',
  'separating',
  'diarizing',
  'transcribing',
  'translating',
  'synthesizing',
  'mixing',
  'completed',
  'failed',
]

type StepState = 'done' | 'active' | 'error' | 'pending'

function getStepState(stepKey: string, jobStatus: string): StepState {
  if (jobStatus === 'failed')    return 'error'
  if (jobStatus === 'completed') return 'done'
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

  const isDone    = job.status === 'completed'
  const isError   = job.status === 'failed'
  const isPending = job.status === 'pending'

  return (
    <div
      className="rounded-lg border p-4"
      style={{ background: 'var(--color-surface-3)', borderColor: 'var(--color-border)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <span className="text-[11px] font-semibold text-white/50 uppercase tracking-wider">AI Pipeline</span>
        {isPending && (
          <div className="flex items-center gap-1 text-[11px] text-white/40">
            <Hourglass size={11} />
            <span>Queued</span>
          </div>
        )}
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
                    background:
                      state === 'done'    ? 'rgba(16,185,129,0.08)'
                    : state === 'active'  ? 'rgba(124,58,237,0.12)'
                    : state === 'error'   ? 'rgba(239,68,68,0.08)'
                    : 'var(--color-surface-4)',
                    borderColor:
                      state === 'done'    ? 'rgba(16,185,129,0.25)'
                    : state === 'active'  ? 'rgba(124,58,237,0.3)'
                    : state === 'error'   ? 'rgba(239,68,68,0.25)'
                    : 'var(--color-border)',
                  }}
                  animate={state === 'active' ? { opacity: [0.7, 1, 0.7] } : {}}
                  transition={{ duration: 1.8, repeat: Infinity }}
                >
                  {state === 'active'
                    ? <Loader2 size={14} className="animate-spin" />
                    : state === 'done'
                    ? <CheckCircle2 size={14} />
                    : state === 'error'
                    ? <AlertCircle size={14} />
                    : <Icon size={14} />
                  }
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
                <div
                  className="flex-1 h-px mt-4 relative overflow-hidden mx-0.5"
                  style={{ background: 'var(--color-border)' }}
                >
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

      {/* Error detail banner */}
      {isError && job.error_msg && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-3 px-3 py-2 rounded-md border text-[11px] text-red-400"
          style={{ background: 'rgba(239,68,68,0.06)', borderColor: 'rgba(239,68,68,0.2)' }}
        >
          {job.error_msg}
        </motion.div>
      )}
    </div>
  )
}

// Compact step-dot row — one dot per real pipeline stage, never a fake
// percentage. The backend only ever knows which discrete stage it's in, not
// how far through that stage it is, so a percentage number necessarily either
// lies (implies precision that doesn't exist) or sits visually frozen for the
// whole stage. Dots only ever communicate what's actually known: done, active
// (animated — no implied duration), or not yet reached.
function PipelineStepperCompact({ job }: { job: Job }) {
  const isFailed = job.status === 'failed'

  return (
    <div className="space-y-1.5">
      <span className="text-[11px] text-white/40 capitalize">
        {isFailed ? 'Failed' : job.status.replace('_', ' ')}
      </span>
      <div className="flex items-center gap-1">
        {STEPS.map((step) => {
          const state = getStepState(step.key, job.status)
          return (
            <div
              key={step.key}
              title={step.label}
              className={cn(
                'h-1.5 flex-1 rounded-full transition-colors duration-300',
                state === 'done'    && 'bg-emerald-500/70',
                state === 'error'   && 'bg-red-500/70',
                state === 'pending' && 'bg-white/10',
              )}
              style={state === 'active' ? { background: 'var(--color-brand, #7C3AED)' } : undefined}
            >
              {state === 'active' && (
                <motion.div
                  className="h-full w-full rounded-full bg-gradient-to-r from-brand to-accent"
                  animate={{ opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: 'easeInOut' }}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
