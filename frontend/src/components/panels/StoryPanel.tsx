// src/components/panels/StoryPanel.tsx
// Story tab of the dock — the project's story notes (backend
// app/services/story_notes.py): the user's intro, plus the characters, words
// to keep, story so far and tone the AI learned from the episodes marked
// finished. Translation and the Assistant both read them. Rows the AI wrote
// carry an "AI" tag; editing one makes it the user's, and learning never
// changes what the user wrote. Marking an episode finished starts learning
// from it, and that progress shows here, under the header, and nowhere else.
import { useState, type ReactNode } from 'react'
import { BookOpen, Check, Loader2, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { useLearnStory, useSetEpisodeFinished, useStory, useUpdateStory } from '@/hooks/useApi'
import { useEditorStore } from '@/store/editorStore'
import { cn } from '@/lib/utils'
import type { StoryBy, StoryEdit, StoryEpisode, StoryView } from '@/types'
import { PanelEmptyState, PanelHeader } from './PanelShell'

const inputClass =
  'w-full bg-ink-950 border border-ink-700/60 rounded px-2 py-1 text-[11px] text-ink-100 placeholder:text-ink-600 ' +
  'focus:outline-none focus:border-brand-500/60 focus:ring-1 focus:ring-brand-500/40'

type Save = (edit: StoryEdit, done: () => void) => void

function AiTag({ by }: { by: StoryBy }) {
  if (by !== 'ai') return null
  return (
    <span
      title="Learned from finished episodes. Edit it to make it yours."
      className="shrink-0 rounded px-1 text-[8.5px] font-semibold text-ink-400 bg-white/5"
    >
      AI
    </span>
  )
}

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="px-3 py-2.5 border-b border-ink-800/50 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[10.5px] font-semibold text-ink-300">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  )
}

function EditButtons({ onSave, onCancel, onDelete, saving, canSave = true }: {
  onSave: () => void; onCancel: () => void; onDelete?: () => void; saving: boolean; canSave?: boolean
}) {
  return (
    <div className="flex items-center gap-1.5 pt-1">
      {onDelete && (
        <button type="button" onClick={onDelete} className="flex items-center gap-1 text-[10px] text-ink-500 hover:text-red-400 transition-colors">
          <Trash2 size={11} /> Delete
        </button>
      )}
      <button type="button" onClick={onCancel} className="ml-auto text-[10px] text-ink-400 hover:text-white px-1.5 py-0.5 transition-colors">
        Cancel
      </button>
      <button
        type="button"
        onClick={onSave}
        disabled={!canSave || saving}
        className="text-[10px] font-semibold text-white bg-brand hover:bg-brand-hover disabled:opacity-40 rounded px-2 py-0.5 transition-colors"
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  )
}

/** The intro or the tone: edit in place, Save appears once it's changed. */
function TextNote({ value, multiline, placeholder, saving, onSave }: {
  value: string; multiline?: boolean; placeholder: string; saving: boolean
  onSave: (text: string, done: () => void) => void
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const changed = draft !== null && draft.trim() !== value
  const save = () => { if (draft !== null) onSave(draft, () => setDraft(null)) }
  const props = {
    className: inputClass,
    value: draft ?? value,
    placeholder,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setDraft(e.target.value),
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') setDraft(null)
      if (e.key === 'Enter' && !multiline && changed) save()
    },
  }
  return (
    <div>
      {multiline ? <textarea rows={4} {...props} className={cn(inputClass, 'resize-y leading-snug')} /> : <input {...props} />}
      {changed && <EditButtons onSave={save} onCancel={() => setDraft(null)} saving={saving} />}
    </div>
  )
}

interface Row { id: string; zh: string; km: string; detail: string; by: StoryBy }
type RowIn = { id?: string; zh: string; km: string; detail: string }

/** Characters or words to keep: click a row to change it; the whole list is saved. */
function ItemRows({ rows, detailPlaceholder, addLabel, saving, onSave }: {
  rows: Row[]; detailPlaceholder: string; addLabel: string; saving: boolean
  onSave: (rows: RowIn[], done: () => void) => void
}) {
  const [editing, setEditing] = useState<string | null>(null)   // a row's id, or 'new'
  const [form, setForm] = useState({ zh: '', km: '', detail: '' })
  const open = (row?: Row) => {
    setEditing(row?.id ?? 'new')
    setForm(row ? { zh: row.zh, km: row.km, detail: row.detail } : { zh: '', km: '', detail: '' })
  }
  const close = () => setEditing(null)
  const plain: RowIn[] = rows.map(({ id, zh, km, detail }) => ({ id, zh, km, detail }))
  const save = () => {
    if (!form.zh.trim()) return
    onSave(editing === 'new' ? [...plain, form] : plain.map((r) => (r.id === editing ? { ...r, ...form } : r)), close)
  }
  const field = (key: keyof typeof form) => ({
    value: form[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [key]: e.target.value })),
  })

  const editor = (
    <div
      className="space-y-1 rounded-md border border-ink-700/60 bg-ink-950/50 p-2"
      onKeyDown={(e) => {
        if (e.key === 'Escape') close()
        // From a box only: Enter on a focused button (Delete) is that button's
        if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') save()
      }}
    >
      <div className="grid grid-cols-2 gap-1">
        <input className={inputClass} lang="zh-Hans" placeholder="Chinese" autoFocus {...field('zh')} />
        <input className={inputClass} lang="km" placeholder="Khmer" {...field('km')} />
      </div>
      <input className={inputClass} placeholder={detailPlaceholder} {...field('detail')} />
      <EditButtons
        onSave={save}
        onCancel={close}
        onDelete={editing !== 'new' ? () => onSave(plain.filter((r) => r.id !== editing), close) : undefined}
        saving={saving}
        canSave={!!form.zh.trim()}
      />
    </div>
  )

  return (
    <div className="space-y-0.5">
      {rows.length === 0 && editing !== 'new' && <p className="text-[10px] text-ink-600">None yet</p>}
      {rows.map((row) => (editing === row.id ? <div key={row.id}>{editor}</div> : (
        <button
          key={row.id}
          type="button"
          onClick={() => open(row)}
          className="w-full text-left rounded px-1.5 py-1 hover:bg-white/5 transition-colors"
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <span lang="zh-Hans" className="text-[11px] text-ink-100 shrink-0">{row.zh}</span>
            {row.km && <span lang="km" className="text-[12px] text-brand-300 truncate">{row.km}</span>}
            <span className="ml-auto"><AiTag by={row.by} /></span>
          </div>
          {row.detail && <p className="text-[10px] text-ink-500 leading-snug">{row.detail}</p>}
        </button>
      )))}
      {editing === 'new' ? editor : (
        <button type="button" onClick={() => open()} className="flex items-center gap-1 text-[10px] text-brand-400 hover:text-brand-300 pt-1 transition-colors">
          <Plus size={11} /> {addLabel}
        </button>
      )}
    </div>
  )
}

function Summaries({ episodes, saving, onSave }: {
  episodes: StoryEpisode[]; saving: boolean; onSave: (jobId: string, text: string, done: () => void) => void
}) {
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const told = episodes.filter((e) => e.summary)
  if (told.length === 0) return <p className="text-[10px] text-ink-600">Fills in as episodes are finished</p>
  return (
    <div className="space-y-0.5">
      {told.map((e) => (editing === e.job_id ? (
        <div key={e.job_id} onKeyDown={(k) => { if (k.key === 'Escape') setEditing(null) }}>
          <textarea rows={3} autoFocus className={cn(inputClass, 'resize-y leading-snug')} value={draft} onChange={(k) => setDraft(k.target.value)} />
          <EditButtons onSave={() => onSave(e.job_id, draft, () => setEditing(null))} onCancel={() => setEditing(null)} saving={saving} />
        </div>
      ) : (
        <button
          key={e.job_id}
          type="button"
          onClick={() => { setEditing(e.job_id); setDraft(e.summary) }}
          className="w-full text-left rounded px-1.5 py-1 hover:bg-white/5 transition-colors"
        >
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-semibold text-ink-400">Episode {e.number}</span>
            <span className="ml-auto"><AiTag by={e.summary_by} /></span>
          </div>
          <p className="text-[10.5px] text-ink-300 leading-snug">{e.summary}</p>
        </button>
      )))}
    </div>
  )
}

/** Under the header: what learning is doing, or why it can't. */
function LearningLine({ story, projectId }: { story: StoryView; projectId: string }) {
  const learn = useLearnStory()
  const waiting = story.episodes.some((e) => e.finished && !e.learned)
  const start = () => learn.mutate(projectId, { onError: (e) => toast.error((e as Error).message || "Couldn't start learning") })
  let line: ReactNode = null
  if (story.learning.state === 'running') {
    line = <span className="shimmer-text font-medium">{story.learning.message}</span>
  } else if (waiting && !story.can_learn) {
    line = (
      <span className="text-ink-400">
        Learning from episodes needs a Gemini API key.{' '}
        <button type="button" onClick={() => useEditorStore.getState().setSettingsOpen(true)} className="text-brand-400 hover:text-brand-300">
          Open Settings
        </button>
      </span>
    )
  } else if (story.learning.state === 'failed') {
    line = (
      <span className="text-amber-300/90">
        {story.learning.message}{' '}
        <button type="button" onClick={start} disabled={learn.isPending} className="underline hover:text-amber-200">Try again</button>
      </span>
    )
  } else if (waiting) {
    line = (
      <button type="button" onClick={start} disabled={learn.isPending} className="text-brand-400 hover:text-brand-300">
        Learn from the finished episodes
      </button>
    )
  }
  if (!line) return null
  return <div className="px-3 py-1.5 border-b border-ink-800/50 text-[10.5px] leading-snug shrink-0">{line}</div>
}

function FinishedEpisodes({ episodes, jobId }: { episodes: StoryEpisode[]; jobId?: string }) {
  const setFinished = useSetEpisodeFinished()
  if (episodes.length === 0) return <p className="text-[10px] text-ink-600">No episodes yet</p>
  return (
    <div className="flex flex-wrap gap-1">
      {episodes.map((e) => (
        <button
          key={e.job_id}
          type="button"
          aria-pressed={e.finished}
          onClick={() => setFinished.mutate(
            { jobId: e.job_id, finished: !e.finished },
            { onError: (err) => toast.error((err as Error).message || "Couldn't change the episode") },
          )}
          title={e.finished ? `Episode ${e.number} is finished. Click to undo.` : `Mark episode ${e.number} finished`}
          className={cn(
            'flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] tabular-nums transition-colors',
            e.finished ? 'bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20' : 'bg-white/5 text-ink-400 hover:bg-white/10 hover:text-white',
            e.job_id === jobId && 'ring-1 ring-brand-500/60',
          )}
        >
          {e.number}
          {e.finished && <Check size={10} />}
        </button>
      ))}
    </div>
  )
}

function StoryNotes({ projectId, jobId }: { projectId: string; jobId?: string }) {
  const { data: story, isLoading, refetch } = useStory(projectId)
  const update = useUpdateStory()
  const save: Save = (edit, done) => update.mutate(
    { projectId, edit },
    { onSuccess: done, onError: (e) => toast.error((e as Error).message || "Couldn't save the story notes") },
  )

  if (!story) {
    return isLoading ? (
      <div className="flex-1 flex items-center justify-center"><Loader2 size={16} className="animate-spin text-ink-600" /></div>
    ) : (
      <PanelEmptyState icon={BookOpen} title="Couldn't load the story notes" actionLabel="Try again" onAction={() => refetch()} />
    )
  }
  const finished = story.episodes.filter((e) => e.finished).length
  return (
    <>
      <LearningLine story={story} projectId={projectId} />
      <div className="flex-1 overflow-y-auto min-h-0">
        <Section title="Your intro" aside={<span className="text-[9.5px] text-ink-600">optional</span>}>
          <TextNote
            multiline
            value={story.intro}
            placeholder="The story, the main characters and how they talk"
            saving={update.isPending}
            onSave={(text, done) => save({ intro: text }, done)}
          />
        </Section>
        <Section title="Tone" aside={story.tone.text ? <AiTag by={story.tone.by} /> : undefined}>
          <TextNote
            value={story.tone.text}
            placeholder="e.g. light romance, polite speech"
            saving={update.isPending}
            onSave={(text, done) => save({ tone: text }, done)}
          />
        </Section>
        <Section title="Characters" aside={<span className="text-[9.5px] text-ink-600 tabular-nums">{story.characters.length || ''}</span>}>
          <ItemRows
            rows={story.characters.map((c) => ({ id: c.id, zh: c.zh, km: c.km, detail: c.about, by: c.by }))}
            detailPlaceholder="Man or woman, age, how they speak and are addressed"
            addLabel="Add character"
            saving={update.isPending}
            onSave={(rows, done) => save({ characters: rows.map(({ id, zh, km, detail }) => ({ id, zh, km, about: detail })) }, done)}
          />
        </Section>
        <Section title="Words to keep" aside={<span className="text-[9.5px] text-ink-600 tabular-nums">{story.words.length || ''}</span>}>
          <ItemRows
            rows={story.words.map((w) => ({ id: w.id, zh: w.zh, km: w.km, detail: w.note, by: w.by }))}
            detailPlaceholder="Note (optional)"
            addLabel="Add word"
            saving={update.isPending}
            onSave={(rows, done) => save({ words: rows.map(({ id, zh, km, detail }) => ({ id, zh, km, note: detail })) }, done)}
          />
        </Section>
        <Section title="Story so far">
          <Summaries
            episodes={story.episodes}
            saving={update.isPending}
            onSave={(jobId, text, done) => save({ summaries: { [jobId]: text } }, done)}
          />
        </Section>
        <Section
          title="Finished episodes"
          aside={story.episodes.length ? <span className="text-[9.5px] text-ink-600 tabular-nums">{finished} of {story.episodes.length}</span> : undefined}
        >
          <FinishedEpisodes episodes={story.episodes} jobId={jobId} />
        </Section>
      </div>
    </>
  )
}

export function StoryPanel({ projectId, jobId }: { projectId?: string; jobId?: string }) {
  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader icon={BookOpen} title="Story" />
      {projectId ? <StoryNotes projectId={projectId} jobId={jobId} /> : <PanelEmptyState icon={BookOpen} title="No project open" />}
    </div>
  )
}
