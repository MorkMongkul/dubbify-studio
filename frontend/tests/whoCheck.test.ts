// "Who is this?": the lines Movelo isn't sure about, their two guesses, the
// characters to pick from and a pick's undo (src/lib/whoCheck.ts).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  basisOf, characterChoices, guessChips, isToCheck, nextToCheck, pickUndoPatches, toCheck, unassignPatches,
} from '../src/lib/whoCheck.ts'
import type { CharacterPicked, Segment, Speaker, WhoGuess } from '../src/types/index.ts'

const line = (id: string, start: number, extra: Partial<Segment> = {}): Segment => ({
  id, job_id: 'j', speaker_id: 'a', voice_id: null, lane_index: 0, start_time: start, end_time: start + 1,
  source_text: '', english_text: '', khmer_text: 'ខ្មែរ', tts_audio_path: '', tts_audio_url: null,
  tts_duration_secs: 0, is_approved: false, notes: '', volume_db: 0, voice_filter: '', voice_speed: 1,
  voice_offset: 0, fit_rate: 1, fade_in_secs: 0, fade_out_secs: 0, who_check: null, who_confirmed: false, ...extra,
})
const guess = (speaker_id: string, basis = 'voice'): WhoGuess => ({ speaker_id, basis })
const speaker = (id: string, display_name = '', label = id.toUpperCase()): Speaker =>
  ({ id, project_id: 'p', label, display_name })

test('lines to check are those with guesses — an empty list too — in time order', () => {
  const segs = [
    line('c', 9, { who_check: [guess('a')] }),
    line('sure', 1),
    line('a', 2, { who_check: [] }),
    line('b', 5, { who_check: [guess('a'), guess('b')] }),
  ]
  assert.deepEqual(toCheck(segs).map((s) => s.id), ['a', 'b', 'c'])
  assert.equal(toCheck(segs).length, 3)
})

test('an empty guess list is still to check; null is sure', () => {
  assert.equal(isToCheck(line('x', 0, { who_check: [] })), true)
  assert.equal(isToCheck(line('x', 0, { who_check: null })), false)
  assert.equal(isToCheck(line('x', 0, { who_check: undefined })), false)
})

test('a confirmed line is never to check', () => {
  const segs = [line('a', 1, { who_check: [guess('a')], who_confirmed: true }), line('b', 2, { who_check: [] })]
  assert.deepEqual(toCheck(segs).map((s) => s.id), ['b'])
})

test('next to check goes forward in time and wraps to the first', () => {
  const segs = [
    line('a', 1, { who_check: [] }),
    line('m', 3),
    line('b', 5, { who_check: [guess('a')] }),
  ]
  assert.equal(nextToCheck(segs, 'a')?.id, 'b')
  assert.equal(nextToCheck(segs, 'b')?.id, 'a')
  // from a sure line: the next to check after it
  assert.equal(nextToCheck(segs, 'm')?.id, 'b')
})

test('next to check skips confirmed lines', () => {
  const segs = [
    line('a', 1, { who_check: [] }),
    line('b', 2, { who_check: [guess('a')], who_confirmed: true }),
    line('c', 3, { who_check: null, who_confirmed: true }),
    line('d', 4, { who_check: [guess('b')] }),
  ]
  assert.equal(nextToCheck(segs, 'a')?.id, 'd')
  assert.equal(nextToCheck(segs, 'd')?.id, 'a')
})

test('a line just picked is left for the next one; with none left there is no next', () => {
  // 'a' was picked: the server answer has who_check null
  const segs = [line('a', 1, { who_check: null, who_confirmed: true }), line('b', 2, { who_check: [] })]
  assert.equal(nextToCheck(segs, 'a')?.id, 'b')
  assert.equal(nextToCheck([line('a', 1, { who_confirmed: true })], 'a'), null)
})

test('lines with a pick on the way are skipped', () => {
  const segs = [line('a', 1, { who_check: [] }), line('b', 2, { who_check: [] }), line('c', 3, { who_check: [] })]
  assert.equal(nextToCheck(segs, 'a', { skip: new Set(['a', 'b']) })?.id, 'c')
  assert.equal(nextToCheck(segs, 'c', { skip: new Set(['a', 'c']) })?.id, 'b')
  assert.equal(nextToCheck(segs, 'a', { skip: new Set(['a', 'b', 'c']) }), null)
})

test('the line being checked stays when it is the only one left', () => {
  const segs = [line('a', 1, { who_check: [] }), line('b', 2)]
  assert.equal(nextToCheck(segs, 'a')?.id, 'a')
})

test('nothing checked yet: the first line to check from the playhead, else the first one', () => {
  const segs = [line('a', 1, { who_check: [] }), line('b', 5, { who_check: [] }), line('c', 9, { who_check: [] })]
  assert.equal(nextToCheck(segs, null)?.id, 'a')
  assert.equal(nextToCheck(segs, null, { from: 3 })?.id, 'b')
  assert.equal(nextToCheck(segs, null, { from: 5 })?.id, 'b')   // a line starting at the playhead counts
  assert.equal(nextToCheck(segs, null, { from: 9.5 })?.id, 'a')  // nothing after: wraps to the first
  // a checked line that's gone (deleted) also starts from the playhead
  assert.equal(nextToCheck(segs, 'gone', { from: 6 })?.id, 'c')
  assert.equal(nextToCheck([line('s', 1)], null), null)
})

test('lines starting together keep one order', () => {
  const segs = [line('b', 2, { who_check: [] }), line('a', 2, { who_check: [] })]
  assert.equal(nextToCheck(segs, 'a')?.id, 'b')
  assert.equal(nextToCheck(segs, 'b')?.id, 'a')
})

test('guess chips: a face, a name, its basis and key 1 or 2', () => {
  const speakers = [speaker('a', 'Heng Dara'), speaker('b', 'Ly Visal')]
  const seg = line('x', 0, { who_check: [guess('a', 'face'), guess('b', 'voice')] })
  const chips = guessChips(seg, speakers)
  assert.deepEqual(chips.map((c) => [c.speaker.id, c.basis, c.key]), [['a', 'face', 1], ['b', 'voice', 2]])
})

test('bases read as "voice + face", "face" or "voice"', () => {
  assert.equal(basisOf('voice + face'), 'voice + face')
  assert.equal(basisOf('voice+face'), 'voice + face')
  assert.equal(basisOf('face'), 'face')
  assert.equal(basisOf('voice'), 'voice')
  assert.equal(basisOf('something new'), 'voice')
})

test('a guess whose character is gone is dropped and the keys follow what is shown', () => {
  const speakers = [speaker('b', 'Ly Visal')]
  const seg = line('x', 0, { who_check: [guess('gone', 'face'), guess('b', 'voice')] })
  assert.deepEqual(guessChips(seg, speakers).map((c) => [c.speaker.id, c.key]), [['b', 1]])
})

test('no guesses, no chips — and never more than two, never one character twice', () => {
  const speakers = [speaker('a'), speaker('b'), speaker('c')]
  assert.deepEqual(guessChips(line('x', 0, { who_check: [] }), speakers), [])
  assert.deepEqual(guessChips(line('x', 0, { who_check: null }), speakers), [])
  const many = line('x', 0, { who_check: [guess('a'), guess('a', 'face'), guess('b'), guess('c')] })
  assert.deepEqual(guessChips(many, speakers).map((c) => c.speaker.id), ['a', 'b'])
})

test("character choices: this episode's first, then every other project character, never twice", () => {
  const project = [speaker('z', 'Zed'), speaker('b', 'Bopha'), speaker('a', 'Arun'), speaker('c', '', 'SPEAKER_10'), speaker('d', '', 'SPEAKER_9')]
  const episode = [line('1', 0, { speaker_id: 'b' }), line('2', 1, { speaker_id: 'z' }), line('3', 2, { speaker_id: 'b' }),
    line('4', 3, { speaker_id: null }), line('5', 4, { speaker_id: 'not-in-project' })]
  const choices = characterChoices(episode, project)
  assert.deepEqual(choices.map((c) => [c.speaker.id, c.inEpisode]),
    [['b', true], ['z', true], ['a', false], ['d', false], ['c', false]])
  assert.equal(new Set(choices.map((c) => c.speaker.id)).size, choices.length)
})

test('a project listing one character twice still shows it once', () => {
  const a = speaker('a', 'Arun')
  assert.deepEqual(characterChoices([line('1', 0, { speaker_id: 'a' })], [a, a]).map((c) => c.speaker.id), ['a'])
})

const picked = (segment: Partial<Segment>, retranslated: boolean, previous_khmer = 'មុន'): CharacterPicked =>
  ({ segment: line('x', 0, segment), retranslated, previous_khmer })

test("undoing a pick puts back the speaker and the check together (the line isn't confirmed)", () => {
  const before = line('x', 0, { speaker_id: 'a', who_check: [guess('a', 'face'), guess('b')], who_confirmed: false })
  const p = pickUndoPatches(before, picked({ speaker_id: 'b', who_check: null, who_confirmed: true }, false))
  assert.deepEqual(p.before, { speaker_id: 'a', who_check: [guess('a', 'face'), guess('b')], who_confirmed: false })
  assert.deepEqual(p.after, { speaker_id: 'b', who_check: null, who_confirmed: true })
})

test('undoing a pick that rewrote the Khmer puts the Khmer back as machine Khmer, on its own track', () => {
  const before = line('x', 0, { speaker_id: null, who_check: [], who_confirmed: false, lane_index: 0, khmer_text: 'ignored' })
  const p = pickUndoPatches(before, picked({ speaker_id: 'b', khmer_text: 'ថ្មី', lane_index: 2 }, true, 'មុន'))
  assert.deepEqual(p.before, {
    speaker_id: null, who_check: [], who_confirmed: false, khmer_text: 'មុន', khmer_machine: 'មុន', lane_index: 0,
  })
  assert.deepEqual(p.after, {
    speaker_id: 'b', who_check: null, who_confirmed: true, khmer_text: 'ថ្មី', khmer_machine: 'ថ្មី', lane_index: 2,
  })
})

test('the patches are copies: a later change to the line does not change the undo', () => {
  const checks = [guess('a')]
  const before = line('x', 0, { who_check: checks })
  const p = pickUndoPatches(before, picked({ speaker_id: 'b' }, false))
  checks.push(guess('b'))
  assert.deepEqual(p.before.who_check, [guess('a')])
})

test('unassigning keeps the check; its undo puts the speaker back without confirming it', () => {
  const before = line('x', 0, { speaker_id: 'a', who_check: [guess('a')], who_confirmed: false })
  assert.deepEqual(unassignPatches(before), {
    before: { speaker_id: 'a', who_check: [guess('a')], who_confirmed: false },
    after: { speaker_id: null },
  })
})
