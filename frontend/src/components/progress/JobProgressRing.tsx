// src/components/progress/JobProgressRing.tsx
// The ring for an episode's running step: the step's title and an estimated
// percentage (lib/jobProgress.ts stagePercent) that keeps moving between the
// 2-second polls, from how long the step has run (step_elapsed_secs) plus the
// time since that answer arrived.
import { useEffect, useState } from 'react'
import { useJob } from '@/hooks/useApi'
import { holdPercent, jobProgress, stagePercent, type ShownPercent } from '@/lib/jobProgress'
import { ProgressRing } from '@/components/ui/ProgressRing'

interface JobProgressRingProps {
  jobId: string
  /** The step just finished: show "Done" (useDoneFlash) */
  done?: boolean
  className?: string
}

export function JobProgressRing({ jobId, done = false, className }: JobProgressRingProps) {
  const { data: job, dataUpdatedAt } = useJob(jobId)
  const [now, setNow] = useState(0)
  useEffect(() => {
    if (done) return
    const tick = () => setNow(Date.now())
    const first = setTimeout(tick, 0)   // at once: a cached answer's age counts from the first frame
    const timer = setInterval(tick, 500)
    return () => {
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [done])

  const status = job?.status ?? ''
  const videoSecs = job?.duration_secs ?? 0
  const ran = job?.step_elapsed_secs
  const estimate = ran == null ? null : stagePercent(status, ran + (now > dataUpdatedAt ? (now - dataUpdatedAt) / 1000 : 0), videoSecs)
  // Never lower within a step, holding while the estimate is missing (the
  // first moment of a new episode, whose start the backend doesn't know)
  const [shown, setShown] = useState<ShownPercent | null>(null)
  const next = holdPercent(shown, status, estimate, stagePercent(status, 0, videoSecs) ?? 0)
  if (next.status !== shown?.status || next.percent !== shown?.percent) setShown(next)
  const title = jobProgress(status, false)?.title ?? 'Working…'
  return <ProgressRing percent={next.percent} title={title} done={done} className={className} />
}
