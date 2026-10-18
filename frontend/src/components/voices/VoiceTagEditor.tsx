// src/components/voices/VoiceTagEditor.tsx
// Edit who a voice sounds like, roughly how old, and its personality and role
// tags. Who + age decide its section in the Voices tab and the voice pickers.
import { useState } from 'react'
import type { VoiceTagValue } from '@/types'
import {
  AGE_GROUPS, GENDERS, MAX_TAGS, MAX_TAG_LENGTH, PERSONALITY_TAGS, ROLE_TAGS, addTag, toggleTag,
} from '@/lib/voiceTags'
import { cn } from '@/lib/utils'

interface Props {
  value: VoiceTagValue
  onChange: (value: VoiceTagValue) => void
  /** Keeps input ids unique when several editors are on screen. */
  idPrefix: string
}

const label = 'text-[10px] font-semibold text-white/50 uppercase tracking-wider'

function choice(active: boolean) {
  return cn(
    'px-2 h-6 rounded-md border text-[10.5px] font-medium transition-colors',
    active ? 'border-brand-400 bg-brand/15 text-white' : 'border-white/10 text-white/55 hover:text-white hover:border-white/20'
  )
}

export function VoiceTagEditor({ value, onChange, idPrefix }: Props) {
  return (
    <div className="space-y-2.5">
      <div>
        <p className={cn(label, 'mb-1')}>Who it sounds like</p>
        <div className="flex flex-wrap gap-1">
          {GENDERS.map((g) => (
            <button
              key={g.value}
              type="button"
              aria-pressed={value.gender === g.value}
              onClick={() => onChange({ ...value, gender: value.gender === g.value ? null : g.value })}
              className={choice(value.gender === g.value)}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>
      <div>
        <p className={cn(label, 'mb-1')}>Age</p>
        <div className="flex flex-wrap gap-1">
          {AGE_GROUPS.map((a) => (
            <button
              key={a.value}
              type="button"
              aria-pressed={value.age_group === a.value}
              onClick={() => onChange({ ...value, age_group: value.age_group === a.value ? null : a.value })}
              className={choice(value.age_group === a.value)}
            >
              {a.label}
            </button>
          ))}
        </div>
      </div>
      <TagPicker
        title="Personality"
        options={PERSONALITY_TAGS}
        tags={value.personality}
        onChange={(personality) => onChange({ ...value, personality })}
        inputId={`${idPrefix}-personality`}
        selectedClass="bg-brand-500/15 text-brand-300 border-brand-400/40"
      />
      <TagPicker
        title="Role"
        options={ROLE_TAGS}
        tags={value.roles}
        onChange={(roles) => onChange({ ...value, roles })}
        inputId={`${idPrefix}-roles`}
        selectedClass="bg-blue-500/10 text-blue-400 border-blue-500/40"
      />
    </div>
  )
}

interface TagPickerProps {
  title: string
  options: string[]
  tags: string[]
  onChange: (tags: string[]) => void
  inputId: string
  selectedClass: string
}

function TagPicker({ title, options, tags, onChange, inputId, selectedClass }: TagPickerProps) {
  const [draft, setDraft] = useState('')
  const has = (tag: string) => tags.some((t) => t.toLowerCase() === tag.toLowerCase())
  const typed = tags.filter((t) => !options.some((o) => o.toLowerCase() === t.toLowerCase()))
  const full = tags.length >= MAX_TAGS
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label htmlFor={inputId} className={label}>{title}</label>
        <span className="text-[9.5px] text-white/30 tabular-nums">{tags.length}/{MAX_TAGS}</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {[...options, ...typed].map((tag) => (
          <button
            key={tag}
            type="button"
            aria-pressed={has(tag)}
            disabled={full && !has(tag)}
            onClick={() => onChange(toggleTag(tags, tag))}
            className={cn(
              'text-[10px] leading-none px-1.5 py-[4px] rounded-full border transition-colors disabled:opacity-35 disabled:cursor-not-allowed',
              has(tag) ? selectedClass : 'border-white/10 text-white/50 hover:text-white hover:border-white/20'
            )}
          >
            {tag}
          </button>
        ))}
      </div>
      <input
        id={inputId}
        value={draft}
        maxLength={MAX_TAG_LENGTH}
        disabled={full}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return
          e.preventDefault()
          onChange(addTag(tags, draft))
          setDraft('')
        }}
        placeholder={full ? `Up to ${MAX_TAGS} tags` : 'Add your own, then press Enter'}
        className="mt-1.5 w-full rounded-md bg-ink-950/60 border border-white/10 px-2 h-7 text-[11px] text-white placeholder-white/25 focus:outline-none focus:border-brand-400 disabled:opacity-50 transition-all"
      />
    </div>
  )
}
