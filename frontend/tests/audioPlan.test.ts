// The preview's sound on one audio clock (src/lib/audioPlan.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  anchorAt, fadePoints, heardAt, LEAD, learnedDelay, loadOrder, mediaSoundStart, previewVoiceUrl, shiftAnchor, soundKey, startTimes,
  stillAnchor, stretchOf, toCtx, toMedia, upcoming, voiceStart,
} from '../src/lib/audioPlan.ts'
import { voiceUrl } from '../src/lib/clipRules.ts'
import type { Segment } from '../src/types/index.ts'

const clip = (fields: Partial<Segment> = {}) => ({
  id: 'a', start_time: 2, end_time: 4, tts_audio_url: '/uploads/p/j/tts/seg_a.wav', tts_duration_secs: 5,
  voice_offset: 0.5, fit_rate: 1.25, volume_db: 0, voice_filter: null, fade_in_secs: 0, fade_out_secs: 0,
  ...fields,
}) as Segment

const near = (a: number, b: number) => assert.ok(Math.abs(a - b) < 1e-9, `${a} ≠ ${b}`)

test('a voice at 1× plays its own file; a faster one is fetched at its speed, made with the export\'s filter', () => {
  const plain = clip({ fit_rate: 1 })
  assert.equal(stretchOf(plain), 1)
  assert.equal(previewVoiceUrl(plain), voiceUrl(plain))
  const fast = clip()
  assert.equal(stretchOf(fast), 1.25)
  const version = voiceUrl(fast).split('?v=')[1]
  assert.equal(previewVoiceUrl(fast), `/api/v1/segments/a/voice?rate=1.2500&v=${encodeURIComponent(version)}`)
})

test('a clip starts at its start with its first kept sound, or mid-way from where the playhead is', () => {
  // the file is stretched by 1.25: half a second of file in is 0.4 s of the stretched voice
  const before = voiceStart(clip(), 0)!
  near(before.at, 2); near(before.offset, 0.4)
  const inside = voiceStart(clip(), 3)!
  near(inside.at, 3); near(inside.offset, 1.4)       // (0.5 + 1 s × 1.25) / 1.25
  const plain = voiceStart(clip({ fit_rate: 1 }), 3)!
  near(plain.offset, 1.5)
  assert.equal(voiceStart(clip(), 4), null)
  assert.equal(voiceStart(clip({ tts_audio_url: null }), 0), null)
})

test('fades are the export\'s straight lines, from where the playhead is', () => {
  const faded = clip({ fade_in_secs: 0.5, fade_out_secs: 1 })
  assert.deepEqual(fadePoints(faded, 0), [[2, 0], [2.5, 1], [3, 1], [4, 0]])
  assert.deepEqual(fadePoints(faded, 3.5), [[3.5, 0.5], [4, 0]])
  assert.deepEqual(fadePoints(clip(), 0), [[2, 1], [4, 1]])
})

test('the audio clock and the video\'s time map both ways, at any speed', () => {
  const a = { ctx: 10, media: 30, rate: 2 }
  near(toMedia(a, toCtx(a, 37)), 37)
  near(toCtx(a, 32), 11)
})

test('what is heard right after starting is where the video is, whatever the output delay', () => {
  const a = anchorAt(5, 12.3, 1, 0.03)
  near(heardAt(a, 5, 0.03), 12.3)
  near(heardAt(a, 6, 0.03), 13.3)
  const fast = anchorAt(5, 12.3, 2, 0.03)
  near(heardAt(fast, 6, 0.03), 14.3)
})

test('a start: the first sound is heard as the first frame shows, whichever needs longer', () => {
  const latency = 0.39                       // Bluetooth earbuds (measured 2026-10-24)
  // A quick picture (0.19 s): the sound goes at once, the picture is held back
  let t = startTimes(latency, 0.19)
  near(t.soundLead, LEAD); near(t.hold, LEAD + latency - 0.19)
  // The user's Safari (~0.5 s): the picture goes at once, the sound waits for it
  t = startTimes(latency, 0.55)
  near(t.hold, 0); near(t.soundLead, 0.55 - latency)
  // Either way, the playhead is what is heard when the first frame shows
  const a = stillAnchor(5, 13.175, 1, t.soundLead)
  near(heardAt(a, 5 + 0.55, latency), 13.175)
  // The Mac's speakers
  near(startTimes(0.01, 0.55).soundLead, 0.54)
})

test('each start teaches how long the picture takes to start; a guess off either way is corrected', () => {
  // Drift = picture − sound. Started 0.17 s later than guessed: the sound ran ahead (drift −0.17)
  near(learnedDelay(0.02, -0.17), 0.19)
  // Started earlier than guessed (the picture ahead)
  near(learnedDelay(0.19, 0.05), 0.14)
  // A stall isn't a start delay: the learning stays within reason
  assert.equal(learnedDelay(0.2, -3), 0.8)
  assert.equal(learnedDelay(0.05, 0.5), 0)
})

test('a correction moves the sound to the picture: what is heard next is where the picture is', () => {
  const a = { ctx: 10, media: 30, rate: 1 }
  const drift = -0.17                        // the picture is 0.17 s behind what is heard
  near(heardAt(shiftAnchor(a, drift), 11, 0.39), heardAt(a, 11, 0.39) + drift)
})

test('a clip is played again when what it plays changes', () => {
  const k = soundKey(clip())
  assert.equal(soundKey(clip()), k)
  assert.notEqual(soundKey(clip({ volume_db: 3 })), k)      // a saved volume re-renders the file
  assert.notEqual(soundKey(clip({ start_time: 2.1 })), k)
  assert.notEqual(soundKey(clip({ voice_offset: 0.6 })), k)
  assert.notEqual(soundKey(clip({ fade_in_secs: 0.2 })), k)
  assert.notEqual(soundKey(clip({ fit_rate: 1.1 })), k)
})

test('the voices to load: those still playing or starting soon', () => {
  const segs = [clip({ id: 'past', start_time: 0, end_time: 1 }), clip({ id: 'now' }), clip({ id: 'soon', start_time: 6, end_time: 7 }),
    clip({ id: 'later', start_time: 20, end_time: 21 }), clip({ id: 'text', tts_audio_url: null })]
  assert.deepEqual(upcoming(segs, 3, 5).map((s) => s.id), ['now', 'soon'])
})

test('an episode\'s voices load from the playhead on, then the ones before it, nearest first', () => {
  const at = (id: string, start: number) => clip({ id, start_time: start, end_time: start + 1 })
  const segs = [at('a', 1), at('b', 9), at('c', 4.5), at('d', 6), at('e', 2), clip({ id: 'text', tts_audio_url: null })]
  assert.deepEqual(loadOrder(segs, 5).map((s) => s.id), ['c', 'd', 'b', 'e', 'a'])   // c is still playing at 5
})

test("a media clip's own sound starts at its start from where the clip begins in the file, or mid-way; never once over", () => {
  const media = { start_time: 10, end_time: 17, source_in: 0.5 }
  assert.deepEqual(mediaSoundStart(media, 8), { at: 10, offset: 0.5 })
  assert.deepEqual(mediaSoundStart(media, 12.25), { at: 12.25, offset: 2.75 })
  assert.equal(mediaSoundStart(media, 17), null)
})
