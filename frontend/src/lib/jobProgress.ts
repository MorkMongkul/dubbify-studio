// src/lib/jobProgress.ts
// What a running step says, in one place: the transcript panel shows it (a
// full view before an episode has lines, a thin bar above the lines after),
// and the top bar only while that panel is hidden. No runtime imports, so
// `npm test` runs it directly.

/** What the user started on an episode — the status alone can't tell a
 *  re-analysis from speaker detection (both run as "diarizing"). */
export type JobAction = 'analyze' | 'detect-speakers' | 'translate-missing'

export type StepState = 'done' | 'active' | 'pending'

export interface ProgressStep {
  label: string
  state: StepState
}

export interface JobProgress {
  title: string
  detail?: string
  /** One dot per step that actually runs; empty for a single-step action. */
  steps: ProgressStep[]
}

// The steps each stage really runs, by status. Voices and mixing are started
// separately, so they're never shown as steps still to come.
const STAGES: { label: string; statuses: string[] }[][] = [
  [
    { label: 'Extract the audio', statuses: ['extracting'] },
    { label: 'Separate music and voices', statuses: ['separating'] },
  ],
  [
    // One request writes down the lines and who says them; "transcribing"
    // only follows for a moment.
    { label: 'Transcribe and find speakers', statuses: ['diarizing', 'transcribing'] },
    { label: 'Translate', statuses: ['translating'] },
  ],
]

const LISTENING = ['diarizing', 'transcribing']

function stepsFor(status: string): ProgressStep[] {
  const stage = status === 'pending' ? STAGES[0] : STAGES.find((s) => s.some((step) => step.statuses.includes(status)))
  if (!stage) return []
  const current = stage.findIndex((step) => step.statuses.includes(status))
  return stage.map((step, i) => ({
    label: step.label,
    state: current < 0 || i > current ? 'pending' : i === current ? 'active' : 'done',
  }))
}

/**
 * The progress to show for a job, or null when nothing it covers is running
 * (voice generation and export have their own progress).
 * `hasLines`: the episode already has lines on screen, which stay while it runs.
 */
export function jobProgress(
  status: string | null | undefined,
  hasLines: boolean,
  action?: JobAction | null,
): JobProgress | null {
  if (!status) return null
  const listening = LISTENING.includes(status)

  if (hasLines) {
    if (status === 'separating') {
      return { title: 'Separating music and voices…', detail: 'Lines, translations and voices are kept', steps: [] }
    }
    if (!listening && status !== 'translating') return null
    if (action === 'analyze') {
      return {
        title: listening ? 'Analyzing again: transcribing and finding speakers…' : 'Analyzing again: translating…',
        detail: "These lines are replaced when it's done",
        steps: [],
      }
    }
    if (listening) {
      return action === 'detect-speakers'
        ? { title: 'Detecting speakers…', detail: 'Text, translations and voices stay as they are', steps: [] }
        : { title: 'Detecting speakers…', steps: [] }
    }
    return { title: action === 'translate-missing' ? 'Translating missing lines…' : 'Translating…', steps: [] }
  }

  switch (status) {
    case 'pending':
      return { title: 'Getting ready…', steps: stepsFor(status) }
    case 'extracting':
      return { title: 'Extracting the audio…', steps: stepsFor(status) }
    case 'separating':
      return { title: 'Separating music and voices…', detail: 'The music and the original voices become separate tracks', steps: stepsFor(status) }
    case 'diarizing':
    case 'transcribing':
      return { title: 'Transcribing and finding speakers…', detail: 'Writing down each line and who says it', steps: stepsFor(status) }
    case 'translating':
      return { title: 'Translating to Khmer…', detail: "The lines appear here when it's done", steps: stepsFor(status) }
    default:
      return null
  }
}
