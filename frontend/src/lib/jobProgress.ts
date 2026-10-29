// src/lib/jobProgress.ts
// What a running step says, in one place: the transcript panel shows it (a
// full view before an episode has lines, a thin bar above the lines after),
// and the top bar only while that panel is hidden. Before an episode has
// lines the full view is a ring with a percentage (stagePercent). No runtime
// imports, so `npm test` runs it directly.

/** What the user started on an episode — the status alone can't tell a
 *  re-analysis from speaker detection (both run as "diarizing"). */
export type JobAction = 'analyze' | 'detect-speakers' | 'translate-missing'

export interface JobProgress {
  title: string
}

export const UPLOADING_TITLE = 'Uploading video…'

const LISTENING = ['diarizing', 'transcribing']

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
      return { title: 'Separating music and voices…' }
    }
    const recognizing = status === 'recognizing'
    if (!listening && !recognizing && status !== 'translating') return null
    if (action === 'analyze') {
      if (recognizing) return { title: 'Analyzing again: recognizing characters…' }
      return { title: listening ? 'Analyzing again: transcribing and finding speakers…' : 'Analyzing again: translating…' }
    }
    if (recognizing) return { title: 'Recognizing characters…' }
    if (listening) return { title: 'Detecting speakers…' }
    return { title: action === 'translate-missing' ? 'Translating missing lines…' : 'Translating…' }
  }

  switch (status) {
    case 'pending':
      return { title: 'Getting ready…' }
    case 'extracting':
      return { title: 'Extracting the audio…' }
    case 'separating':
      return { title: 'Separating music and voices…' }
    case 'diarizing':
    case 'transcribing':
      return { title: 'Transcribing and finding speakers…' }
    case 'recognizing':
      return { title: 'Recognizing characters…' }
    case 'translating':
      return { title: 'Translating to Khmer…' }
    default:
      return null
  }
}

// ── The ring's percentage ───────────────────────────────────────────────────
//
// The steps run on services that don't say how far along they are (a short
// episode goes to Gemini in one request), so the ring estimates: each step
// owns a slice of the ring and fills it at the pace the step usually takes for
// a video that long, measured on real episodes (a 3-minute one: separation
// about 2 min 20 s, transcription about 1 min, translation a few seconds).
// A step that runs long slows down near the end of its slice and waits there;
// only the next step, or Done, moves past it. Two rings: a new video (upload,
// audio, music and voices) and Analyze (transcribe and find speakers, recognize
// characters, translate); Detect speakers is one step, so its ring is 0–100.

interface RingStep {
  from: number
  to: number
  /** Seconds the step usually takes, by the video's length in seconds. */
  usualSecs: (videoSecs: number) => number
}

const UPLOAD_END = 5
const RING_STEPS: Record<string, RingStep> = {
  pending:      { from: 5,  to: 6,   usualSecs: () => 2 },
  extracting:   { from: 6,  to: 12,  usualSecs: (v) => 3 + 0.02 * v },
  // The separation service has a fixed start-up (a 30-second clip took about 70 s)
  separating:   { from: 12, to: 100, usualSecs: (v) => 45 + 0.55 * v },
  diarizing:    { from: 0,  to: 55,  usualSecs: (v) => 15 + 0.3 * v },
  // Right after listening: line timing finds where each line's words are heard (6.4 s for 113 s on an M1)
  transcribing: { from: 55, to: 60,  usualSecs: (v) => 2 + 0.04 * v },
  // Voice only since 0.5.2, measured on an M1: 11 s for a 129 s video (34 lines; with faces it took 2–3 min)
  recognizing:  { from: 60, to: 85,  usualSecs: (v) => 3 + 0.07 * v },
  translating:  { from: 85, to: 100, usualSecs: (v) => 5 + 0.04 * v },
}
/** Detect speakers runs only this step, so it fills a ring of its own. */
const DETECT_RING: RingStep = { ...RING_STEPS.recognizing, from: 0, to: 100 }

const SHORT_EPISODE_SECS = 180

/** How much of its slice a step has filled: on pace up to 80%, then slowing toward 95% and waiting there. */
function filled(fractionOfUsual: number): number {
  const f = Math.max(0, fractionOfUsual)
  return f <= 0.8 ? f : 0.8 + 0.15 * (1 - Math.exp(-(f - 0.8) / 0.3))
}

/**
 * The ring's percentage (0–99) for a running step that has been going for
 * `elapsedSecs`, on a video `videoSecs` long; null when no ring step runs.
 */
export function stagePercent(
  status: string | null | undefined,
  elapsedSecs: number,
  videoSecs: number,
  action?: JobAction | null,
): number | null {
  const step = status === 'recognizing' && action === 'detect-speakers' ? DETECT_RING : status ? RING_STEPS[status] : undefined
  if (!step) return null
  const usual = step.usualSecs(videoSecs > 0 ? videoSecs : SHORT_EPISODE_SECS)
  return Math.min(99, Math.floor(step.from + filled(elapsedSecs / usual) * (step.to - step.from)))
}

/** The upload's part of a new video's ring (it's measured: `fraction` of the file sent). */
export function uploadPercent(fraction: number): number {
  return Math.floor(Math.min(1, Math.max(0, fraction)) * UPLOAD_END)
}

export interface ShownPercent {
  status: string
  percent: number
}

/**
 * What the ring shows next: within a step it never goes down (an answer can
 * arrive a little late) and holds while the estimate is missing; a new step
 * starts from its estimate, or from where that step begins (`stepStart`).
 */
export function holdPercent(prev: ShownPercent | null, status: string, estimate: number | null, stepStart: number): ShownPercent {
  if (prev && prev.status === status) {
    return { status, percent: Math.max(prev.percent, estimate ?? prev.percent) }
  }
  return { status, percent: estimate ?? stepStart }
}
