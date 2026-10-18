// The built-in voice pack's tags must be ones the tag editor offers, spelled
// the same way: a typo like "Narator" would quietly move a voice out of its
// section. (Run with `npm test`.)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { PERSONALITY_TAGS, ROLE_TAGS } from '../src/lib/voiceTags.ts'

interface ManifestVoice { id: string; personality?: string[]; roles?: string[] }

const manifest = JSON.parse(readFileSync(
  new URL('../../backend/app/assets/builtin_voices/manifest.json', import.meta.url), 'utf8',
)) as { voices: ManifestVoice[] }

test('every built-in voice tag is one the tag editor offers', () => {
  const unknown = manifest.voices.flatMap((v) => [
    ...(v.personality ?? []).filter((t) => !PERSONALITY_TAGS.includes(t)).map((t) => `${v.id}: personality "${t}"`),
    ...(v.roles ?? []).filter((t) => !ROLE_TAGS.includes(t)).map((t) => `${v.id}: role "${t}"`),
  ])
  assert.deepEqual(unknown, [])
})
