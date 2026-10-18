// Run with `npm test` (Node's own test runner; Node strips the TypeScript types).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ageLabel, groupVoices, pickerLabel, voiceMatches } from '../src/lib/voiceTags.ts'
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

test('voices are grouped: My voices first, then sections in order, own voices first, then by age and name', () => {
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
    ['young_men', 'Young men', ['Zed', 'Heng Dara', 'Ly Visal', 'Lim Rithy']],
    ['older_women', 'Older women', ['Taing Thida']],
  ])
  assert.equal(groups[1].hint, '20s to 30s')
})

test('a built-in voice that has no section yet is never shown as one of mine', () => {
  const untagged = voice({ id: 'builtin-99-a', builtin: true, name: 'Untagged' })
  assert.deepEqual(groupVoices([untagged]).map((g) => g.key), [])
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
