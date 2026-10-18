// src/lib/voiceTags.ts
// Tags that help pick a voice for an actor, and the sections they sort voices
// into in the Voices tab and the voice pickers. The backend decides each
// voice's section (app/services/voice_tags.py); this names the sections and
// orders the voices inside them. Kept free of runtime imports so
// tests/voiceTags.test.ts can run it with plain Node.
import type { Voice, VoiceAgeGroup, VoiceGender, VoiceSectionKey, VoiceTagValue } from '@/types'

export const SECTIONS: { key: VoiceSectionKey; label: string; hint?: string }[] = [
  { key: 'young_men', label: 'Young men', hint: '20s to 30s' },
  { key: 'young_women', label: 'Young women', hint: '20s to 30s' },
  { key: 'older_men', label: 'Older men', hint: '40s to 50s' },
  { key: 'older_women', label: 'Older women', hint: '40s to 50s' },
  { key: 'elders', label: 'Elders', hint: '60s and up' },
  { key: 'teens_children', label: 'Teens and children' },
  { key: 'narrators', label: 'Narrators' },
]

export const GENDERS: { value: VoiceGender; label: string }[] = [
  { value: 'man', label: 'Man' },
  { value: 'woman', label: 'Woman' },
  { value: 'boy', label: 'Boy' },
  { value: 'girl', label: 'Girl' },
]

export const AGE_GROUPS: { value: VoiceAgeGroup; label: string }[] = [
  { value: 'child', label: 'Child' },
  { value: 'teen', label: 'Teen' },
  { value: '20s', label: '20s' },
  { value: '30s', label: '30s' },
  { value: '40s', label: '40s' },
  { value: '50s', label: '50s' },
  { value: '60s', label: '60s' },
  { value: '70s+', label: '70s+' },
]

export const PERSONALITY_TAGS = [
  'Warm', 'Gentle', 'Bright', 'Sweet', 'Lively', 'Cold', 'Calm', 'Deep', 'Firm', 'Soft', 'Kind', 'Sly',
  'Elegant', 'Menacing', 'Serious', 'Wise', 'Cheerful', 'Playful', 'Funny', 'Regal', 'Commanding', 'Rough',
  'Timid', 'Reassuring',
]

export const ROLE_TAGS = [
  'Lead', 'Second lead', 'Hero', 'Heroine', 'Villain', 'Schemer', 'Sidekick', 'Best friend', 'Father', 'Mother',
  'Grandparent', 'Emperor', 'Empress', 'General', 'Warrior', 'Servant', 'Maid', 'Doctor', 'CEO', 'Student',
  'Child', 'Narrator',
]

/** Per kind of tag, as the backend allows. */
/** What a voice says in a sample made for listening ("Hello, this is a test voice from Movelo Studio."). */
export const SAMPLE_LINE = 'សួស្តី នេះគឺជាសំឡេងសាកល្បងពី Movelo Studio។'

export const MAX_TAGS = 6
export const MAX_TAG_LENGTH = 24

export const NO_TAGS: VoiceTagValue = { gender: null, age_group: null, personality: [], roles: [] }

/** A voice's tags as the editor holds them (voices made before tags have none). */
export function tagsOf(v: Voice): VoiceTagValue {
  return { gender: v.gender ?? null, age_group: v.age_group ?? null,
    personality: v.personality ?? [], roles: v.roles ?? [] }
}

/** Add a typed tag, tidied; repeats (ignoring case), overlong tags and a full list leave `tags` as it was. */
export function addTag(tags: string[], raw: string): string[] {
  const tag = raw.split(/\s+/).filter(Boolean).join(' ')
  const repeat = tags.some((t) => t.toLowerCase() === tag.toLowerCase())
  if (!tag || repeat || tag.length > MAX_TAG_LENGTH || tags.length >= MAX_TAGS) return tags
  return [...tags, tag]
}

/** A clicked tag: removed when the voice has it (in any case), added otherwise. */
export function toggleTag(tags: string[], tag: string): string[] {
  const kept = tags.filter((t) => t.toLowerCase() !== tag.toLowerCase())
  return kept.length < tags.length ? kept : addTag(tags, tag)
}

/** Why these tags can't be saved yet, or null. */
export function tagsBlocker(tags: VoiceTagValue): string | null {
  return tags.gender && !tags.age_group ? 'Choose an age for this voice' : null
}

export type VoiceGroupKey = 'mine' | VoiceSectionKey | 'other'

export interface VoiceGroup {
  key: VoiceGroupKey
  label: string
  hint?: string
  voices: Voice[]
}

/** "Man, 20s", "Girl, child"; just "Man" or "Teen" when one half is missing. */
export function ageLabel(v: Pick<Voice, 'gender' | 'age_group'>): string | null {
  const who = GENDERS.find((g) => g.value === v.gender)?.label
  const age = AGE_GROUPS.find((a) => a.value === v.age_group)?.label
  if (who && age) return `${who}, ${age.toLowerCase()}`
  return who ?? age ?? null
}

export function sectionLabel(key: VoiceSectionKey | null | undefined): string {
  return SECTIONS.find((s) => s.key === key)?.label ?? 'My voices'
}

const AGE_ORDER = AGE_GROUPS.map((a) => a.value)
const newestFirst = (a: Voice, b: Voice) => b.created_at.localeCompare(a.created_at)

/**
 * Inside a section: the user's own voices first (newest first), then built-in
 * ones by age, then in library order (their ids run by character, then take),
 * which keeps each character's takes together; their names are arbitrary.
 */
function withinSection(a: Voice, b: Voice): number {
  if (!a.builtin !== !b.builtin) return a.builtin ? 1 : -1
  if (!a.builtin) return newestFirst(a, b)
  const byAge = AGE_ORDER.indexOf(a.age_group as VoiceAgeGroup) - AGE_ORDER.indexOf(b.age_group as VoiceAgeGroup)
  return byAge || a.id.localeCompare(b.id)
}

/**
 * "My voices" (own voices with no section yet), then each section in order,
 * then "Other voices": any other voice without a known section (a built-in
 * voice whose tags didn't install), so no voice ever disappears from the tab
 * or the pickers. Empty groups are left out.
 */
export function groupVoices(voices: Voice[]): VoiceGroup[] {
  const known = new Set<string>(SECTIONS.map((s) => s.key))
  const mine = (v: Voice) => !v.builtin && !v.section
  const groups: VoiceGroup[] = [
    { key: 'mine', label: 'My voices', voices: voices.filter(mine).sort(newestFirst) },
    ...SECTIONS.map((s) => ({ ...s, voices: voices.filter((v) => v.section === s.key).sort(withinSection) })),
    { key: 'other', label: 'Other voices',
      voices: voices.filter((v) => !mine(v) && !known.has(v.section ?? '')).sort(withinSection) },
  ]
  return groups.filter((g) => g.voices.length > 0)
}

/**
 * Search by name, description, tags, who ("man, 40s") or section ("older
 * men"), ignoring case. The query must start a word, so "man" finds men but
 * not "woman".
 */
export function voiceMatches(v: Voice, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  const atWordStart = new RegExp(`(^|[^\\p{L}\\p{N}])${q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`, 'u')
  const fields = [v.name, v.description, ageLabel(v), v.section ? sectionLabel(v.section) : null,
    ...(v.personality ?? []), ...(v.roles ?? [])]
  return fields.some((field) => atWordStart.test((field ?? '').toLowerCase()))
}

export type VoiceFilterKey = 'all' | 'men' | 'women' | 'kids' | 'elders' | 'narrators' | 'mine'

/** The voice browser's quick filters: whole sections, or every voice of the user's own. */
export const VOICE_FILTERS: { key: VoiceFilterKey; label: string; sections?: VoiceSectionKey[] }[] = [
  { key: 'all', label: 'All' },
  { key: 'men', label: 'Men', sections: ['young_men', 'older_men'] },
  { key: 'women', label: 'Women', sections: ['young_women', 'older_women'] },
  { key: 'kids', label: 'Kids & teens', sections: ['teens_children'] },
  { key: 'elders', label: 'Elders', sections: ['elders'] },
  { key: 'narrators', label: 'Narrators', sections: ['narrators'] },
  { key: 'mine', label: 'Mine' },
]

/** The voices a filter and a search (voiceMatches) both let through, in their original order. */
export function filterVoices(voices: Voice[], query: string, filter: VoiceFilterKey): Voice[] {
  const sections = VOICE_FILTERS.find((f) => f.key === filter)?.sections
  return voices.filter((v) => {
    if (filter === 'mine' && v.builtin) return false
    if (sections && !sections.includes(v.section as VoiceSectionKey)) return false
    return voiceMatches(v, query)
  })
}

/**
 * Which speakers use each voice, by name ({voice id: names}), leaving out one
 * speaker (the one choosing). Pass the episode's speakers.
 */
export function voiceUsers(
  speakers: { id: string; label: string; display_name?: string | null; voice_id?: string | null }[],
  exceptSpeakerId?: string | null,
): Record<string, string[]> {
  const users: Record<string, string[]> = {}
  for (const sp of speakers) {
    if (!sp.voice_id || sp.id === exceptSpeakerId) continue
    ;(users[sp.voice_id] ??= []).push(sp.display_name?.trim() || sp.label)
  }
  return users
}
