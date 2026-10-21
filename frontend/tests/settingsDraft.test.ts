// Run with `npm test` (Node's own test runner; Node strips the TypeScript types).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { groupChanges } from '../src/lib/settingsDraft.ts'

const SAVED = {
  VOXCPM2_API_URL: '',
  VOXCPM2_HF_SPACE_FALLBACK: 'openbmb/VoxCPM2',
  TRANSLATION_BACKEND: 'gemini' as const,
  GEMINI_MODEL: '',
  GEMINI_ASR_MODEL: '',
  GEMINI_CHAT_MODEL: '',
  GEMINI_TTS_MODEL: '',
  TTS_BATCH_CONCURRENCY: 3,
}

test("Save sends only what changed in the open group", () => {
  const draft = {
    GEMINI_API_KEY: 'AI-new-key',
    VOXCPM2_API_URL: 'https://voices.example.com/',
    TTS_BATCH_CONCURRENCY: 4,
    GEMINI_ASR_MODEL: 'gemini-pro-latest',
  }
  assert.deepEqual(groupChanges(draft, SAVED, 'voices'), {
    VOXCPM2_API_URL: 'https://voices.example.com/',
    TTS_BATCH_CONCURRENCY: 4,
  })
  assert.deepEqual(groupChanges(draft, SAVED, 'keys'), { GEMINI_API_KEY: 'AI-new-key' })
  assert.deepEqual(groupChanges(draft, SAVED, 'models'), { GEMINI_ASR_MODEL: 'gemini-pro-latest' })
  for (const group of ['appearance', 'storage', 'about'] as const) {
    assert.deepEqual(groupChanges(draft, SAVED, group), {}, group)
  }
})

test('a blank key box is never sent, so Save never removes a saved key', () => {
  assert.deepEqual(groupChanges({ GEMINI_API_KEY: '', HF_TOKEN: '   ' }, SAVED, 'keys'), {})
  assert.deepEqual(groupChanges({ HF_TOKEN: '  hf-token  ' }, SAVED, 'keys'), { HF_TOKEN: 'hf-token' })
})

test('a setting changed back to its saved value is not a change', () => {
  const draft = { VOXCPM2_API_URL: '', TTS_BATCH_CONCURRENCY: 3, TRANSLATION_BACKEND: 'gemini' as const }
  assert.deepEqual(groupChanges(draft, SAVED, 'voices'), {})
  assert.deepEqual(groupChanges(draft, SAVED, 'models'), {})
})

test('a pasted link loses the spaces around it, and clearing it is a change', () => {
  assert.deepEqual(groupChanges({ VOXCPM2_API_URL: '  https://voices.example.com/ \n' }, SAVED, 'voices'), {
    VOXCPM2_API_URL: 'https://voices.example.com/',
  })
  const withServer = { ...SAVED, VOXCPM2_API_URL: 'https://voices.example.com/' }
  assert.deepEqual(groupChanges({ VOXCPM2_API_URL: ' ' }, withServer, 'voices'), { VOXCPM2_API_URL: '' })
})
