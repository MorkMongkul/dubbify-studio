// Run with `npm test` (Node's own test runner; Node strips the TypeScript types).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { addTag, ageLabel, groupVoices, pickerLabel, tagsBlocker, tagsOf, toggleTag, voiceMatches } from '../src/lib/voiceTags.ts'
import type { Voice } from '../src/types/index.ts'

let n = 0
function voice(overrides: Partial<Voice>): Voice {
  n += 1
  return {
    id: `v${n}`, name: `Voice ${n}`, mode: 'design', description: '', reference_audio_path: '',
    reference_transcript: '', cfg_value: 2, inference_timesteps: 10, seed: 1,
    created_at: '2026-10-01T00:00:00', builtin: false, gender: null, age_group: null,
    personality: [], roles: [], section: null, ...overrides,
  }
}

test('age label reads who first, then roughly how old', () => {
  assert.equal(ageLabel(voice({ gender: 'man', age_group: '20s' })), 'Man, 20s')
  assert.equal(ageLabel(voice({ gender: 'girl', age_group: 'child' })), 'Girl, child')
  assert.equal(ageLabel(voice({ gender: 'woman', age_group: '70s+' })), 'Woman, 70s+')
  assert.equal(ageLabel(voice({ gender: 'man' })), 'Man')
  assert.equal(ageLabel(voice({ age_group: 'teen' })), 'Teen')
  assert.equal(ageLabel(voice({})), null)
})

test('voices are grouped: My voices first, then sections in order, own voices first, then by age and library order', () => {
  const unsortedOld = voice({ name: 'Old mine', created_at: '2026-10-01T00:00:00' })
  const unsortedNew = voice({ name: 'New mine', created_at: '2026-10-05T00:00:00' })
  const rithy = voice({ id: 'builtin-01-a', builtin: true, name: 'Lim Rithy', gender: 'man', age_group: '30s', section: 'young_men' })
  const visal = voice({ id: 'builtin-01-b', builtin: true, name: 'Ly Visal', gender: 'man', age_group: '20s', section: 'young_men' })
  const dara = voice({ id: 'builtin-01-c', builtin: true, name: 'Heng Dara', gender: 'man', age_group: '20s', section: 'young_men' })
  const mineYoung = voice({ name: 'Zed', gender: 'man', age_group: '30s', section: 'young_men', created_at: '2026-10-03T00:00:00' })
  const empress = voice({ id: 'builtin-19-a', builtin: true, name: 'Taing Thida', gender: 'woman', age_group: '40s', section: 'older_women' })

  const groups = groupVoices([empress, rithy, unsortedOld, visal, mineYoung, dara, unsortedNew])
  assert.deepEqual(groups.map((g) => [g.key, g.label, g.voices.map((v) => v.name)]), [
    ['mine', 'My voices', ['New mine', 'Old mine']],
    ['young_men', 'Young men', ['Zed', 'Ly Visal', 'Heng Dara', 'Lim Rithy']],   // 20s by id (-b, -c), then 30s
    ['older_women', 'Older women', ['Taing Thida']],
  ])
  assert.equal(groups[1].hint, '20s to 30s')
})

test('voices without a known section still show, under Other voices, and never as one of mine', () => {
  const untagged = voice({ id: 'builtin-99-a', builtin: true, name: 'Untagged' })
  const unknown = voice({ name: 'From a newer app', gender: 'man', section: 'martians' as never })
  const mine = voice({ name: 'Mine' })
  assert.deepEqual(groupVoices([untagged, unknown, mine]).map((g) => [g.key, g.label, g.voices.map((v) => v.name)]), [
    ['mine', 'My voices', ['Mine']],
    ['other', 'Other voices', ['From a newer app', 'Untagged']],
  ])
})

test('picker labels show the name, who and the first two personality tags', () => {
  const dara = voice({ builtin: true, name: 'Heng Dara', gender: 'man', age_group: '20s',
    personality: ['Warm', 'Gentle', 'Bright'], section: 'young_men' })
  assert.equal(pickerLabel(dara), 'Heng Dara — Man, 20s, warm, gentle')
  const mineSorted = voice({ name: 'Kim Sothy (copy)', gender: 'man', age_group: '50s', personality: ['Regal'], section: 'older_men' })
  assert.equal(pickerLabel(mineSorted), 'Kim Sothy (copy) (yours) — Man, 50s, regal')
  assert.equal(pickerLabel(voice({ name: 'Plain' })), 'Plain')
  assert.equal(pickerLabel(voice({ name: 'Moody', personality: ['Dark'] })), 'Moody — dark')
})

test('search matches name, description, tags, who and section, ignoring case', () => {
  const villain = voice({ builtin: true, name: 'Sieng Virak', description: 'A deep, gravelly voice.', gender: 'man',
    age_group: '40s', personality: ['Menacing', 'Cold'], roles: ['Villain'], section: 'older_men' })
  for (const q of ['cold', 'VILLAIN', 'virak', 'gravelly', 'man, 40s', 'older men', '']) {
    assert.equal(voiceMatches(villain, q), true, q)
  }
  for (const q of ['narrator', 'woman', 'young men']) {
    assert.equal(voiceMatches(villain, q), false, q)
  }
})

test('adding a typed tag tidies it and ignores repeats, overlong tags and a full list', () => {
  assert.deepEqual(addTag(['Warm'], '  Very   gentle '), ['Warm', 'Very gentle'])
  assert.deepEqual(addTag(['Warm'], 'warm'), ['Warm'])
  assert.deepEqual(addTag(['Warm'], '   '), ['Warm'])
  assert.deepEqual(addTag([], 'x'.repeat(25)), [])
  assert.deepEqual(addTag(['a', 'b', 'c', 'd', 'e', 'f'], 'g'), ['a', 'b', 'c', 'd', 'e', 'f'])
})

test('clicking a tag adds it, and clicking it again removes it whatever its case', () => {
  assert.deepEqual(toggleTag(['Calm'], 'Warm'), ['Calm', 'Warm'])
  assert.deepEqual(toggleTag(['Warm', 'Calm'], 'warm'), ['Calm'])
})

test('a who without an age blocks saving; no who at all is fine', () => {
  const tags = { gender: null, age_group: null, personality: [], roles: [] }
  assert.equal(tagsBlocker({ ...tags, gender: 'man' }), 'Choose an age for this voice')
  assert.equal(tagsBlocker({ ...tags, gender: 'man', age_group: '20s' }), null)
  assert.equal(tagsBlocker(tags), null)
})

test('a voice made before tags existed edits as empty tags', () => {
  assert.deepEqual(tagsOf(voice({ gender: undefined, personality: undefined, roles: undefined })),
    { gender: null, age_group: null, personality: [], roles: [] })
  assert.deepEqual(tagsOf(voice({ gender: 'woman', age_group: '40s', personality: ['Calm'], roles: ['Empress'] })),
    { gender: 'woman', age_group: '40s', personality: ['Calm'], roles: ['Empress'] })
})

test('search matches the start of words, so "man" finds men but not women', () => {
  const sophea = voice({ builtin: true, name: 'Tan Sophea', description: 'A young woman with a bright voice.',
    gender: 'woman', age_group: '20s', personality: ['Sweet'], roles: ['Lead'], section: 'young_women' })
  const dara = voice({ builtin: true, name: 'Heng Dara', description: 'A young man with a warm voice.',
    gender: 'man', age_group: '20s', personality: ['Warm'], roles: ['Lead'], section: 'young_men' })
  for (const q of ['man', 'men', 'young men', 'man, 2']) {
    assert.deepEqual([voiceMatches(sophea, q), voiceMatches(dara, q)], [false, true], q)
  }
  for (const q of ['woman', 'women', 'young women', 'soph']) {
    assert.deepEqual([voiceMatches(sophea, q), voiceMatches(dara, q)], [true, false], q)
  }
  assert.equal(voiceMatches(sophea, 'ophea'), false, 'the middle of a word is not a match')
})
