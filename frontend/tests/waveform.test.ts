// How a clip's or a piece's waveform follows its volume line (src/lib/waveform.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  CEILING, dbLabel, gainFor, levelBars, peaksFrom, slicePeaks, volumeAfterDrag, volumeTop,
} from '../src/lib/waveform.ts'

test('the loudness limit matches the export', () => {
  const py = readFileSync(new URL('../../backend/app/services/voice_level.py', import.meta.url), 'utf8')
  assert.match(py, /CEILING_DB = -1\.0/)
  assert.ok(Math.abs(CEILING - 10 ** (-1 / 20)) < 1e-12)
})

test('a gain in dB scales the amplitude', () => {
  assert.equal(gainFor(0), 1)
  assert.ok(Math.abs(gainFor(6) - 1.9953) < 1e-4)
  assert.ok(Math.abs(gainFor(-20) - 0.1) < 1e-12)
})

test('bars grow with the volume and turn loud past the limit', () => {
  const peaks = [0.1, 0.5, 0.8]
  assert.deepEqual(levelBars(peaks, 1).map((b) => b.loud), [false, false, false])
  const louder = levelBars(peaks, gainFor(6))
  assert.ok(louder[1].h > 0.99 && louder[1].h <= 1)   // 0.5 × 2.0 → full height, clamped
  assert.deepEqual(louder.map((b) => b.loud), [false, true, true])
  assert.ok(levelBars(peaks, gainFor(-12))[2].h < 0.21)
})

test('silence draws as a thin line, never as nothing', () => {
  assert.equal(levelBars([0], 1)[0].h, 0.04)
})

test('a voice the export already holds at its limit is not red', () => {
  // The leveller caps peaks at −1 dBFS and rounds to 0.1 dB, decoding adds a little
  assert.equal(levelBars([CEILING * 10 ** (0.1 / 20)], 1)[0].loud, false)
  assert.equal(levelBars([0.97], 1)[0].loud, true)
})

test('dragging the volume line: 2 px per dB from where it was grabbed, after a 3 px dead zone', () => {
  assert.equal(volumeAfterDrag(0, 0), 0)
  assert.equal(volumeAfterDrag(0, -2), 0)     // a click's jitter changes nothing
  assert.equal(volumeAfterDrag(0, -4), 2)     // up = louder
  assert.equal(volumeAfterDrag(0, 4), -2)
  assert.equal(volumeAfterDrag(-3, -7), 1)    // 7 px up = 3.5 → 4 dB: every whole dB is reachable
  assert.equal(volumeAfterDrag(8, -20), 10)   // stops at the top
  assert.equal(volumeAfterDrag(-18, 40), -20) // and at the bottom
  assert.equal(volumeTop(0, 30), 10)          // 0 dB sits a third of the way down its band
  assert.equal(volumeTop(-20, 30), 30)
})

test('dB labels read like CapCut', () => {
  assert.equal(dbLabel(3), '+3 dB')
  assert.equal(dbLabel(-5), '−5 dB')
  assert.equal(dbLabel(0), '0 dB')
})

test('peaks are absolute, the loudest channel, per time step', () => {
  const left = new Float32Array([0.1, -0.2, 0.05, 0.0])
  const right = new Float32Array([0.0, 0.3, -0.6, 0.1])
  // 4 samples at 4 Hz, 2 peaks per second → 2 samples per peak
  assert.deepEqual(peaksFrom([left, right], 4, 2), [0.3, 0.6].map((x) => Math.fround(x)))
})

test('a piece draws its own part of the stem', () => {
  const peaks = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]   // 10 per second, 1 s
  assert.deepEqual(slicePeaks(peaks, 10, 0.2, 0.6, 2), [0.3, 0.5])  // max of [0.2,0.3] and [0.4,0.5]
  assert.deepEqual(slicePeaks(peaks, 10, 0.95, 2.0, 2), [0.9])      // past the end: what exists
})
