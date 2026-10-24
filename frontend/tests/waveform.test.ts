// How a clip's or a piece's waveform follows its volume line (src/lib/waveform.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  CEILING, dbLabel, gainFor, levelBars, peaksFrom, slicePeaks, volumeAtPointer, volumeTop,
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

test('the volume line follows the pointer inside its band', () => {
  // band 30 px tall from y=100: top = +10 dB, bottom = −20 dB
  assert.equal(volumeAtPointer(100, 100, 30), 10)
  assert.equal(volumeAtPointer(130, 100, 30), -20)
  assert.equal(volumeAtPointer(120, 100, 30), -10)
  assert.equal(volumeAtPointer(40, 100, 30), 10)    // above the band: the loudest
  assert.equal(volumeTop(0, 30), 10)                // 0 dB sits a third of the way down
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
