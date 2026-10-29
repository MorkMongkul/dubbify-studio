// Run with `npm test` (Node's own test runner; Node strips the TypeScript types).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { holdPercent, jobProgress, stagePercent, uploadPercent } from '../src/lib/jobProgress.ts'

test('a first analysis says what the long step really does, in plain words', () => {
  assert.equal(jobProgress('diarizing', false)!.title, 'Transcribing and finding speakers…')
  assert.equal(jobProgress('transcribing', false)!.title, 'Transcribing and finding speakers…')
  assert.equal(jobProgress('translating', false)!.title, 'Translating to Khmer…')
  for (const status of ['diarizing', 'transcribing', 'translating']) {
    assert.doesNotMatch(jobProgress(status, false)!.title, /diariz/i)
  }
})

test('with lines on screen, the bar names the action that was started', () => {
  assert.equal(jobProgress('diarizing', true, 'detect-speakers')!.title, 'Detecting speakers…')

  assert.equal(jobProgress('diarizing', true, 'analyze')!.title, 'Analyzing again: transcribing and finding speakers…')
  assert.equal(jobProgress('translating', true, 'analyze')!.title, 'Analyzing again: translating…')

  assert.equal(jobProgress('translating', true, 'translate-missing')!.title, 'Translating missing lines…')

  assert.equal(jobProgress('separating', true)!.title, 'Separating music and voices…')
})

test("without a known action, or one that can't be running now, the bar claims nothing it doesn't know", () => {
  // After a reload the started action is unknown
  assert.equal(jobProgress('diarizing', true)!.title, 'Detecting speakers…')
  assert.equal(jobProgress('translating', true)!.title, 'Translating…')
  // Speaker detection never translates: an older action doesn't describe this run
  assert.equal(jobProgress('translating', true, 'detect-speakers')!.title, 'Translating…')
  assert.equal(jobProgress('diarizing', true, 'translate-missing')!.title, 'Detecting speakers…')
})

test('nothing to show when no step is running that this progress covers', () => {
  for (const status of ['stems_ready', 'completed', 'failed', 'synthesizing', 'mixing', '']) {
    assert.equal(jobProgress(status, false), null, status)
    assert.equal(jobProgress(status, true, 'analyze'), null, status)
    assert.equal(stagePercent(status, 30, 180), null, status)
  }
  assert.equal(jobProgress(undefined, false), null)
  assert.equal(jobProgress(null, true), null)
  assert.equal(stagePercent(undefined, 30, 180), null)
  assert.equal(stagePercent(null, 30, 180), null)
})

// ── The ring's percentage ───────────────────────────────────────────────────

const NEW_VIDEO = ['pending', 'extracting', 'separating']
const ANALYZE = ['diarizing', 'transcribing', 'recognizing', 'translating']

test("the ring only moves forward within a step, and a step never finishes by itself", () => {
  for (const status of [...NEW_VIDEO, ...ANALYZE]) {
    let last = -1
    for (let t = 0; t <= 7200; t += 3) {
      const p = stagePercent(status, t, 180)!
      assert.ok(p >= last, `${status} went back at ${t}s`)
      assert.ok(p <= 99, `${status} said ${p}% while still working`)
      last = p
    }
  }
})

test('each step starts where the one before it stopped, so the ring never jumps back', () => {
  for (const steps of [NEW_VIDEO, ANALYZE]) {
    for (let i = 1; i < steps.length; i++) {
      const longest = stagePercent(steps[i - 1], 1e6, 180)!
      assert.ok(longest <= stagePercent(steps[i], 0, 180)!, `${steps[i - 1]} → ${steps[i]}`)
    }
  }
  // A new video: the upload, then the audio, then music and voices
  assert.equal(uploadPercent(0), 0)
  assert.equal(uploadPercent(0.5), 2)
  assert.equal(uploadPercent(1), 5)
  assert.ok(uploadPercent(1) <= stagePercent('pending', 0, 180)!)
  assert.equal(stagePercent('separating', 0, 180), 12)
  // Analyze starts a fresh ring
  assert.equal(stagePercent('diarizing', 0, 180), 0)
})

test('a step that runs long waits near its end instead of claiming to be done', () => {
  assert.equal(stagePercent('separating', 1e6, 180), 95)
  assert.ok(stagePercent('diarizing', 1e6, 180)! < stagePercent('transcribing', 0, 180)!)
})

test('it moves at the pace the step usually takes for a video that long', () => {
  // About halfway through listening (0–55%) at half its usual time
  const usual = 15 + 0.3 * 180
  assert.equal(stagePercent('diarizing', usual / 2, 180), 27)
  // The same wait on a longer episode has got less far
  assert.ok(stagePercent('separating', 60, 45 * 60)! < stagePercent('separating', 60, 180)!)
  // An unknown length counts as a short episode
  assert.equal(stagePercent('diarizing', 30, 0), stagePercent('diarizing', 30, 180))
})

test('what the ring shows never goes down within a step, and holds while the estimate is missing', () => {
  let shown = holdPercent(null, 'diarizing', 40, 0)
  assert.deepEqual(shown, { status: 'diarizing', percent: 40 })
  shown = holdPercent(shown, 'diarizing', 38, 0)        // an answer that took a little longer to arrive
  assert.equal(shown.percent, 40)
  shown = holdPercent(shown, 'diarizing', null, 0)      // unknown for a moment
  assert.equal(shown.percent, 40)
  shown = holdPercent(shown, 'diarizing', 45, 0)
  assert.equal(shown.percent, 45)
  // The next step, before its time is known: where that step starts
  shown = holdPercent(shown, 'translating', null, 85)
  assert.deepEqual(shown, { status: 'translating', percent: 85 })
})


// ── Recognizing characters ──────────────────────────────────────────────────

test('recognizing characters has its own words and its slice of the ring', () => {
  const p = stagePercent('recognizing', 30, 100)!
  assert.ok(p >= 60 && p <= 85, `recognizing for 30 s of a 100 s video showed ${p}%`)
  assert.equal(jobProgress('recognizing', false)!.title, 'Recognizing characters…')
  assert.equal(jobProgress('recognizing', true)!.title, 'Recognizing characters…')
  assert.equal(jobProgress('recognizing', true, 'detect-speakers')!.title, 'Recognizing characters…')
  assert.equal(jobProgress('recognizing', true, 'analyze')!.title, 'Analyzing again: recognizing characters…')
  assert.equal(jobProgress('recognizing', true, 'translate-missing')!.title, 'Recognizing characters…')
})

test('transcribing, recognizing and translating run in that order and the ring never goes back', () => {
  for (const [a, b] of [['diarizing', 'transcribing'], ['transcribing', 'recognizing'], ['recognizing', 'translating']]) {
    assert.ok(stagePercent(a, 1e6, 129)! <= stagePercent(b, 0, 129)!, `${a} → ${b}`)
  }
  assert.equal(stagePercent('translating', 0, 129), 85)
})

test("recognizing goes at the pace measured by voice only (3 s + 0.07 s per second of video)", () => {
  const usual = 3 + 0.07 * 129
  const half = stagePercent('recognizing', usual / 2, 129)!
  assert.equal(half, 60 + Math.floor(0.5 * 25))
  assert.ok(stagePercent('recognizing', usual * 5, 129)! < 85)
})

test("Detect speakers' recognizing fills the whole ring", () => {
  assert.equal(stagePercent('recognizing', 0, 129, 'detect-speakers'), 0)
  const mid = stagePercent('recognizing', (3 + 0.07 * 129) / 2, 129, 'detect-speakers')!
  assert.ok(mid >= 45 && mid <= 55, String(mid))
  assert.ok(stagePercent('recognizing', 1e6, 129, 'detect-speakers')! <= 99)
  // Re-analysis keeps the analysis ring
  assert.equal(stagePercent('recognizing', 0, 129, 'analyze'), 60)
})

test("transcribing includes line timing: 2 s + 0.04 s per second of video (6.4 s measured for 113 s)", () => {
  const usual = 2 + 0.04 * 113
  assert.equal(stagePercent('transcribing', usual / 2, 113), 55 + Math.floor(0.5 * 5))
})
