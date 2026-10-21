// src/lib/settingsDraft.ts
// What the Settings window's Save sends. Each group saves its own fields;
// edits wait in a draft until then, so switching groups keeps them. No runtime
// imports, so `npm test` runs it directly.
import type { AppSettingsUpdate, AppSettingsValues } from '@/types'

export type SettingsGroup = 'appearance' | 'keys' | 'voices' | 'models' | 'storage' | 'about'

const GROUP_FIELDS: Record<SettingsGroup, readonly (keyof AppSettingsUpdate)[]> = {
  appearance: [],
  keys: ['GEMINI_API_KEY', 'HF_TOKEN'],
  voices: ['VOXCPM2_API_URL', 'VOXCPM2_HF_SPACE_FALLBACK', 'TTS_BATCH_CONCURRENCY'],
  models: ['TRANSLATION_BACKEND', 'GEMINI_MODEL', 'GEMINI_ASR_MODEL', 'GEMINI_CHAT_MODEL', 'GEMINI_TTS_MODEL'],
  storage: [],
  about: [],
}

const SECRETS: ReadonlySet<string> = new Set(['GEMINI_API_KEY', 'HF_TOKEN'])

/**
 * The fields of `group` the draft changes. A key is sent only when something
 * was typed: the API removes a key it receives empty (that's what Remove does).
 */
export function groupChanges(draft: AppSettingsUpdate, saved: AppSettingsValues, group: SettingsGroup): AppSettingsUpdate {
  const changes: Record<string, string | number> = {}
  for (const key of GROUP_FIELDS[group]) {
    const raw = draft[key]
    if (raw === undefined) continue
    const value = typeof raw === 'string' ? raw.trim() : raw
    if (SECRETS.has(key)) {
      if (value) changes[key] = value
    } else if (value !== saved[key as keyof AppSettingsValues]) {
      changes[key] = value
    }
  }
  return changes as AppSettingsUpdate
}
