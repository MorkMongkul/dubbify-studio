// src/components/panels/VoicesPanel.tsx
// Voices tab of the dock — build, preview, name and manage reusable VoxCPM2
// voices without leaving the editor. Absorbed from the former /voices page;
// every behaviour is preserved (blob preview lifecycle, two-step delete with
// auto-revert, seed reroll, inline rename, blocker-driven save), compacted
// from the page's two columns into the dock's single one. Voices are listed in
// sections by age and gender (src/lib/voiceTags.ts), each with its tags.
import { useState, useRef, useEffect, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Mic, Upload, Loader2, Trash2, Plus, CopyPlus, Dices, Hash, Tag,
  Search, SlidersHorizontal, ChevronDown, RotateCcw, X, Check, AudioLines,
} from 'lucide-react'
import { toast } from 'sonner'
import { useVoices, useCreateVoice, useDeleteVoice, usePreviewVoice, useUpdateVoice, useCopyVoice } from '@/hooks/useApi'
import type { Voice, VoiceMode, VoiceTagValue } from '@/types'
import { Tooltip } from '@/components/ui/Tooltip'
import { cn } from '@/lib/utils'
import { NO_TAGS, groupVoices, sectionLabel, tagsBlocker, tagsOf, voiceMatches } from '@/lib/voiceTags'
import { PanelHeader } from './PanelShell'
import { VoicePlayer } from '@/components/voices/VoicePlayer'
import { VoiceSection } from '@/components/voices/VoiceSection'
import { VoiceTagEditor } from '@/components/voices/VoiceTagEditor'
import { VoiceTags } from '@/components/voices/VoiceTags'

const MODES: { value: VoiceMode; label: string; blurb: string; needs: string }[] = [
  { value: 'design',   label: 'Design',   blurb: 'Create a brand-new voice from a written description alone.', needs: 'Description only' },
  { value: 'clone',    label: 'Clone',    blurb: 'Copy a real voice from an audio clip, with optional style guidance.', needs: 'Needs a clip' },
  { value: 'ultimate', label: 'Ultimate', blurb: 'Clip + its exact transcript, for the highest-fidelity copy.', needs: 'Needs clip + text' },
]

const PREVIEW_DEFAULT = 'សួស្តី នេះគឺជាសំឡេងសាកល្បងពី Movelo Studio។'

type ModeFilter = 'all' | VoiceMode

/** Axios errors carry the backend's `detail` in `message` (see api/client.ts). */
function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

export function VoicesPanel() {
  const { data: voiceList = [], isLoading } = useVoices()
  const createVoice = useCreateVoice()
  const deleteVoice = useDeleteVoice()
  const previewVoice = usePreviewVoice()
  const updateVoice = useUpdateVoice()
  const copyVoice = useCopyVoice()

  const [createOpen, setCreateOpen] = useState(false)
  const [mode, setMode] = useState<VoiceMode>('design')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [transcript, setTranscript] = useState('')
  const [cfg, setCfg] = useState(2.0)
  const [steps, setSteps] = useState(10)
  const [refFile, setRefFile] = useState<File | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [previewText, setPreviewText] = useState(PREVIEW_DEFAULT)
  const [previewingId, setPreviewingId] = useState<string | null>(null)
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({})
  // The line each generated preview says
  const [previewTexts, setPreviewTexts] = useState<Record<string, string>>({})
  const [editingVoiceId, setEditingVoiceId] = useState<string | null>(null)
  const [editingVoiceName, setEditingVoiceName] = useState('')
  const [search, setSearch] = useState('')
  const [modeFilter, setModeFilter] = useState<ModeFilter>('all')
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  // The voice whose sample was just made — its player starts by itself
  const [justGeneratedId, setJustGeneratedId] = useState<string | null>(null)
  const [tags, setTags] = useState<VoiceTagValue>(NO_TAGS)           // new voice's tags
  // Open sections ('mine' = My voices); while searching every section is open
  const [openSections, setOpenSections] = useState<Set<string>>(() => new Set(['mine']))
  const [taggingId, setTaggingId] = useState<string | null>(null)     // own voice whose tags are being edited
  const [tagDraft, setTagDraft] = useState<VoiceTagValue>(NO_TAGS)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const needsRef = mode === 'clone' || mode === 'ultimate'
  const needsTranscript = mode === 'ultimate'

  // Blob URLs for generated previews are revoked when replaced and on unmount
  // (which now also fires on every rail-tab switch — previews stop and their
  // multi-MB decoded buffers free immediately, which is the desired behaviour).
  const previewUrlsRef = useRef(previewUrls)
  useEffect(() => { previewUrlsRef.current = previewUrls }, [previewUrls])
  useEffect(() => () => {
    Object.values(previewUrlsRef.current).forEach((url) => URL.revokeObjectURL(url))
  }, [])

  // A pending delete reverts on its own — no modal, no stuck scary state.
  useEffect(() => {
    if (!confirmDeleteId) return
    const t = setTimeout(() => setConfirmDeleteId(null), 4000)
    return () => clearTimeout(t)
  }, [confirmDeleteId])

  const resetForm = () => {
    setName(''); setDescription(''); setTranscript(''); setCfg(2.0); setSteps(10)
    setRefFile(null); setMode('design'); setShowAdvanced(false); setTags(NO_TAGS)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // Surfaced before the user clicks Save (button state + helper line).
  const blocker =
    !name.trim() ? 'Give the voice a name to save it'
    : needsRef && !refFile ? 'Upload a reference clip for this mode'
    : needsTranscript && !transcript.trim() ? 'Add the transcript of your reference clip'
    : tagsBlocker(tags)

  // A voice that just moved somewhere: open that section so it stays in view.
  const showSection = (v: Voice) => setOpenSections((prev) => new Set(prev).add(v.section ?? 'mine'))

  const handleSave = async () => {
    if (blocker) return
    try {
      const voice = await createVoice.mutateAsync({
        name: name.trim(), mode, description, reference_transcript: transcript,
        cfg_value: cfg, inference_timesteps: steps, reference_audio: refFile, ...tags,
      })
      showSection(voice)
      toast.success(`Voice "${voice.name}" saved — it's in ${sectionLabel(voice.section)}`)
      resetForm()
      setCreateOpen(false)
    } catch (e) {
      toast.error(errorMessage(e, 'Failed to save voice'))
    }
  }

  const handleCopy = async (v: Voice) => {
    try {
      const copy = await copyVoice.mutateAsync(v.id)
      showSection(copy)
      toast.success(`Copied as “${copy.name}” — it's in ${sectionLabel(copy.section)}`)
    } catch (e) {
      toast.error(errorMessage(e, "Couldn't copy this voice"))
    }
  }

  const saveTags = (v: Voice) => {
    if (tagsBlocker(tagDraft)) return
    updateVoice.mutate(
      { id: v.id, data: tagDraft },
      {
        onSuccess: (updated) => {
          setTaggingId(null)
          showSection(updated)
          toast.success(`Tags saved — it's in ${sectionLabel(updated.section)}`)
        },
        onError: (e) => toast.error(errorMessage(e, "Couldn't save the tags")),
      }
    )
  }

  const handlePreview = async (voice: Voice) => {
    if (!previewText.trim()) return toast.error('Add a preview line first')
    setPreviewingId(voice.id)
    try {
      const blob = await previewVoice.mutateAsync({ id: voice.id, text: previewText })
      const url = URL.createObjectURL(blob)
      setPreviewUrls((prev) => {
        if (prev[voice.id]) URL.revokeObjectURL(prev[voice.id])
        return { ...prev, [voice.id]: url }
      })
      setPreviewTexts((prev) => ({ ...prev, [voice.id]: previewText }))
      setJustGeneratedId(voice.id)
    } catch (e) {
      toast.error(errorMessage(e, 'Preview failed — the voice backend may be cold-starting. Try again.'))
    } finally {
      setPreviewingId(null)
    }
  }

  const counts = useMemo(() => ({
    all: voiceList.length,
    design: voiceList.filter((v) => v.mode === 'design').length,
    clone: voiceList.filter((v) => v.mode === 'clone').length,
    ultimate: voiceList.filter((v) => v.mode === 'ultimate').length,
  }), [voiceList])

  // What a voice can play right now: a preview it already made of the current
  // line, or (with the default line) its own sample clip. Otherwise Preview
  // has to generate one first.
  const playableUrl = (v: Voice): string | null => {
    if (previewUrls[v.id] && previewTexts[v.id] === previewText) return previewUrls[v.id]
    if (previewText === PREVIEW_DEFAULT && v.reference_audio_url) return `${v.reference_audio_url}?v=${v.seed}`
    return null
  }

  const player = (v: Voice, bars: number) => (
    <VoicePlayer
      key={`${v.id}:${playableUrl(v) ?? ''}`}
      id={v.id}
      name={v.name}
      url={playableUrl(v)}
      onGenerate={() => handlePreview(v)}
      generating={previewingId === v.id}
      busy={previewingId !== null}
      autoPlay={justGeneratedId === v.id}
      bars={bars}
    />
  )

  const visibleVoices = useMemo(
    () => voiceList.filter((v) => (modeFilter === 'all' || v.mode === modeFilter) && voiceMatches(v, search)),
    [voiceList, search, modeFilter]
  )
  const groups = useMemo(() => groupVoices(visibleVoices), [visibleVoices])
  const searching = search.trim().length > 0
  const toggleSection = (key: string) => setOpenSections((prev) => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })

  const activeMode = MODES.find((m) => m.value === mode)!

  const iconButton = 'flex items-center justify-center h-7 w-7 rounded-md bg-white/[0.04] hover:bg-white/10 text-white/45 hover:text-white transition-colors disabled:opacity-40'

  // A built-in voice: listen, make an editable copy, or copy its ID
  const builtinRow = (v: Voice) => (
    <div
      key={v.id}
      className="rounded-lg border p-2.5"
      style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}
    >
      <p className="text-[12.5px] font-semibold text-white">{v.name}</p>
      <VoiceTags voice={v} className="mt-1" />
      <div className="flex items-center gap-1 mt-2">
        {player(v, 32)}
        <Tooltip content="Make a copy you can edit">
          <button
            onClick={() => handleCopy(v)}
            disabled={copyVoice.isPending}
            aria-label={`Make a copy of ${v.name}`}
            className={cn(iconButton, 'h-6 w-6')}
          >
            <CopyPlus size={10} />
          </button>
        </Tooltip>
        <Tooltip content="Copy voice ID">
          <button
            onClick={() => { navigator.clipboard.writeText(v.id); toast.success('Voice ID copied') }}
            aria-label={`Copy ${v.name} ID`}
            className={cn(iconButton, 'h-6 w-6')}
          >
            <Hash size={10} />
          </button>
        </Tooltip>
      </div>
    </div>
  )

  // One of the user's own voices: rename, tags, reroll, copy, delete
  const ownCard = (v: Voice) => {
    const confirming = confirmDeleteId === v.id
    const tagging = taggingId === v.id
    const tagBlocker = tagsBlocker(tagDraft)
    return (
      <div
        key={v.id}
        className="group rounded-lg border p-2.5 transition-colors hover:border-white/[0.14]"
        style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}
      >
        <div className="flex items-center gap-1.5 flex-wrap">
          {editingVoiceId === v.id ? (
            <input
              autoFocus
              value={editingVoiceName}
              onChange={(e) => setEditingVoiceName(e.target.value)}
              onBlur={() => {
                const trimmed = editingVoiceName.trim()
                if (trimmed && trimmed !== v.name) {
                  updateVoice.mutate(
                    { id: v.id, data: { name: trimmed } },
                    { onError: () => toast.error('Failed to rename voice') }
                  )
                }
                setEditingVoiceId(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
                if (e.key === 'Escape') setEditingVoiceId(null)
              }}
              className="text-[12px] font-semibold text-white bg-ink-950 rounded px-1.5 py-0.5 min-w-0 flex-1 focus:outline-none border border-brand-400/60"
            />
          ) : (
            <button
              className="text-[12px] font-semibold text-white truncate hover:text-brand-200 transition-colors text-left min-w-0 flex-1"
              onClick={() => { setEditingVoiceId(v.id); setEditingVoiceName(v.name) }}
              title="Click to rename"
            >
              {v.name}
            </button>
          )}
          {v.section && (
            <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-white/10 text-white/70 uppercase tracking-wide font-semibold shrink-0">
              Yours
            </span>
          )}
          <span className="text-[8px] px-1.5 py-0.5 rounded-full bg-brand/15 text-brand-300 border border-brand-400/20 uppercase tracking-wide font-semibold shrink-0">
            {v.mode}
          </span>
        </div>
        {v.description && (
          <p className="text-[10px] text-white/40 mt-0.5 line-clamp-1 leading-relaxed" title={v.description}>
            {v.description}
          </p>
        )}
        <VoiceTags voice={v} className="mt-1.5" />

        {/* Actions */}
        <div className="flex items-center gap-1 mt-2">
          {player(v, 32)}

          <Tooltip content="Edit tags">
            <button
              onClick={() => {
                if (tagging) return setTaggingId(null)
                setTagDraft(tagsOf(v))
                setTaggingId(v.id)
              }}
              aria-label={`Edit tags of ${v.name}`}
              aria-expanded={tagging}
              className={cn(iconButton, tagging && 'bg-brand/15 text-brand-300')}
            >
              <Tag size={11} />
            </button>
          </Tooltip>

          <Tooltip content="Reroll voice identity">
            <button
              onClick={() => {
                const newSeed = Math.floor(Math.random() * 2_000_000_000) + 1
                updateVoice.mutate(
                  { id: v.id, data: { seed: newSeed } },
                  {
                    onSuccess: () => {
                      setPreviewUrls((p) => {
                        if (p[v.id]) URL.revokeObjectURL(p[v.id])
                        const n = { ...p }; delete n[v.id]; return n
                      })
                      toast.success('New voice rolled — preview to hear it')
                    },
                  }
                )
              }}
              aria-label="Reroll voice identity"
              className={iconButton}
            >
              <Dices size={12} />
            </button>
          </Tooltip>

          <Tooltip content="Make a copy you can edit">
            <button
              onClick={() => handleCopy(v)}
              disabled={copyVoice.isPending}
              aria-label={`Make a copy of ${v.name}`}
              className={iconButton}
            >
              <CopyPlus size={11} />
            </button>
          </Tooltip>

          <Tooltip content="Copy voice ID">
            <button
              onClick={() => { navigator.clipboard.writeText(v.id); toast.success('Voice ID copied') }}
              aria-label="Copy voice ID"
              className={iconButton}
            >
              <Hash size={11} />
            </button>
          </Tooltip>

          {/* Two-step delete — same safety as confirm(), no dialog */}
          {confirming ? (
            <div className="flex items-center gap-1">
              <button
                onClick={() => { deleteVoice.mutate(v.id); setConfirmDeleteId(null) }}
                className="flex items-center gap-0.5 px-1.5 h-7 rounded-md bg-red-500 hover:bg-red-400 text-white text-[10px] font-semibold transition-colors"
              >
                <Check size={10} /> Delete
              </button>
              <button
                onClick={() => setConfirmDeleteId(null)}
                aria-label="Cancel delete"
                className="flex items-center justify-center h-7 w-7 rounded-md bg-white/[0.04] hover:bg-white/10 text-white/50"
              >
                <X size={11} />
              </button>
            </div>
          ) : (
            <Tooltip content="Delete voice">
              <button
                onClick={() => setConfirmDeleteId(v.id)}
                aria-label="Delete voice"
                className="flex items-center justify-center h-7 w-7 rounded-md bg-white/[0.04] hover:bg-red-500/20 text-white/45 hover:text-red-400 transition-colors"
              >
                <Trash2 size={11} />
              </button>
            </Tooltip>
          )}
        </div>

        {tagging && (
          <div className="mt-2.5 pt-2.5 border-t border-white/[0.07] space-y-2.5">
            <VoiceTagEditor value={tagDraft} onChange={setTagDraft} idPrefix={`tags-${v.id}`} />
            <div className="flex items-center gap-1.5 flex-wrap">
              <button
                onClick={() => saveTags(v)}
                disabled={!!tagBlocker || updateVoice.isPending}
                className="flex items-center gap-1 px-2.5 h-7 rounded-md bg-brand-400 hover:bg-brand-300 text-white text-[10.5px] font-semibold disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {updateVoice.isPending ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                Save tags
              </button>
              <button
                onClick={() => setTaggingId(null)}
                className="px-2 h-7 rounded-md text-[10.5px] text-white/50 hover:text-white hover:bg-white/[0.06] transition-colors"
              >
                Cancel
              </button>
              {tagBlocker && <span className="text-[10px] text-amber-400/70">{tagBlocker}</span>}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader
        icon={Mic}
        title="Voices"
        count={voiceList.length}
        action={
          <button
            onClick={() => setCreateOpen((o) => !o)}
            aria-expanded={createOpen}
            className={cn(
              'flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded transition-colors',
              createOpen
                ? 'text-ink-400 hover:text-ink-200 hover:bg-white/8'
                : 'text-brand-400 hover:text-brand-300 hover:bg-brand-500/10'
            )}
          >
            {createOpen ? <X size={12} /> : <Plus size={12} />}
            <span>{createOpen ? 'Close' : 'New'}</span>
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* ── New Voice (collapsible) ─────────────────────────── */}
        <AnimatePresence initial={false}>
          {createOpen && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.18 }}
              className="overflow-hidden"
            >
              <div
                className="rounded-xl border p-3 space-y-3"
                style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}
              >
                {/* Mode — each option states what it needs up front */}
                <div className="space-y-1.5">
                  <label className="text-[10px] font-semibold text-white/50 uppercase tracking-wider">Method</label>
                  <div className="grid grid-cols-3 gap-1.5">
                    {MODES.map((m) => {
                      const active = mode === m.value
                      return (
                        <button
                          key={m.value}
                          onClick={() => setMode(m.value)}
                          aria-pressed={active}
                          className={cn(
                            'rounded-lg border px-1.5 py-1.5 text-left transition-all',
                            active
                              ? 'border-brand-400 bg-brand/15'
                              : 'border-white/10 hover:border-white/20 hover:bg-white/[0.04]'
                          )}
                        >
                          <div className={cn('text-[10.5px] font-semibold', active ? 'text-white' : 'text-white/70')}>
                            {m.label}
                          </div>
                          <div className={cn('text-[8.5px] leading-tight mt-0.5', active ? 'text-brand-200/80' : 'text-white/35')}>
                            {m.needs}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                  <p className="text-[10px] text-white/45 leading-relaxed">{activeMode.blurb}</p>
                </div>

                {/* Name */}
                <div>
                  <label htmlFor="voice-name" className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-1 block">
                    Voice name <span className="text-brand-300">*</span>
                  </label>
                  <input
                    id="voice-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Hero — Male, Deep"
                    className="w-full rounded-lg bg-ink-950/60 border border-white/10 px-2.5 h-8 text-xs text-white placeholder-white/25 focus:outline-none focus:border-brand-400 transition-all"
                  />
                </div>

                {/* Reference audio (clone/ultimate) */}
                {needsRef && (
                  <div>
                    <label className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-1 block">
                      Reference clip <span className="text-brand-300">*</span>
                    </label>
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      className={cn(
                        'w-full flex items-center justify-center gap-1.5 rounded-lg border border-dashed py-2.5 text-[11px] transition-all',
                        refFile
                          ? 'border-brand-400/50 bg-brand/10 text-white'
                          : 'border-white/15 text-white/45 hover:text-white hover:border-brand-400/60 hover:bg-white/[0.03]'
                      )}
                    >
                      {refFile ? <AudioLines size={12} className="text-brand-300" /> : <Upload size={12} />}
                      <span className="truncate max-w-[180px]">{refFile ? refFile.name : 'Choose a .wav or .mp3 clip'}</span>
                    </button>
                    {refFile && (
                      <button
                        onClick={() => { setRefFile(null); if (fileInputRef.current) fileInputRef.current.value = '' }}
                        className="text-[10px] text-white/40 hover:text-white mt-1 inline-flex items-center gap-1"
                      >
                        <X size={9} /> Remove clip
                      </button>
                    )}
                    <input ref={fileInputRef} type="file" accept="audio/*" className="hidden"
                      onChange={(e) => setRefFile(e.target.files?.[0] || null)} />
                    <p className="text-[9.5px] text-white/30 mt-1 leading-relaxed">
                      5–15 seconds of clean speech works best.
                    </p>
                  </div>
                )}

                {/* Transcript (ultimate) */}
                {needsTranscript && (
                  <div>
                    <label htmlFor="voice-transcript" className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-1 block">
                      What the clip says <span className="text-brand-300">*</span>
                    </label>
                    <textarea
                      id="voice-transcript"
                      value={transcript}
                      onChange={(e) => setTranscript(e.target.value)}
                      placeholder="Type the exact words spoken in your reference clip"
                      rows={2}
                      className="w-full rounded-lg bg-ink-950/60 border border-white/10 px-2.5 py-2 text-xs text-white placeholder-white/25 focus:outline-none focus:border-brand-400 resize-none transition-all"
                    />
                  </div>
                )}

                {/* Description / control instruction */}
                <div>
                  <label htmlFor="voice-description" className="text-[10px] font-semibold text-white/50 uppercase tracking-wider mb-1 block">
                    {mode === 'design' ? 'Describe the voice' : 'Style guidance'}
                    {mode !== 'design' && <span className="text-white/25 normal-case tracking-normal font-normal"> — optional</span>}
                  </label>
                  <textarea
                    id="voice-description"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder={mode === 'design'
                      ? 'A young woman, gentle and sweet, speaks slowly'
                      : 'e.g. cheerful tone, slightly faster pace'}
                    rows={2}
                    className="w-full rounded-lg bg-ink-950/60 border border-white/10 px-2.5 py-2 text-xs text-white placeholder-white/25 focus:outline-none focus:border-brand-400 resize-none transition-all"
                  />
                  {mode === 'design' && !description.trim() && (
                    <p className="text-[9.5px] text-white/30 mt-1 leading-relaxed">
                      Leave blank for a random voice identity — or describe age, gender, tone and pace.
                    </p>
                  )}
                </div>

                {/* Tags — sort the voice into its section */}
                <VoiceTagEditor value={tags} onChange={setTags} idPrefix="new-voice" />

                {/* Advanced — collapsed by default */}
                <div className="rounded-lg border border-white/[0.07] overflow-hidden">
                  <button
                    onClick={() => setShowAdvanced((s) => !s)}
                    aria-expanded={showAdvanced}
                    className="w-full flex items-center gap-1.5 px-2.5 py-2 text-[10.5px] text-white/50 hover:text-white hover:bg-white/[0.03] transition-colors"
                  >
                    <SlidersHorizontal size={11} />
                    <span className="font-medium">Advanced</span>
                    <span className="text-white/25 font-mono text-[9.5px]">CFG {cfg.toFixed(1)} · {steps} steps</span>
                    <ChevronDown size={12} className={cn('ml-auto transition-transform', showAdvanced && 'rotate-180')} />
                  </button>
                  <AnimatePresence initial={false}>
                    {showAdvanced && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.18 }}
                        className="overflow-hidden"
                      >
                        <div className="px-2.5 pb-3 pt-1 space-y-3">
                          <div>
                            <div className="flex items-center justify-between text-[10px] mb-1">
                              <span className="text-white/50">Guidance (CFG)</span>
                              <span className="text-white font-mono">{cfg.toFixed(1)}</span>
                            </div>
                            <input type="range" min={0.5} max={5} step={0.1} value={cfg}
                              onChange={(e) => setCfg(+e.target.value)} className="w-full accent-brand-400 cursor-pointer" />
                            <p className="text-[9px] text-white/25 mt-0.5">Higher follows your description more strictly.</p>
                          </div>
                          <div>
                            <div className="flex items-center justify-between text-[10px] mb-1">
                              <span className="text-white/50">Quality steps</span>
                              <span className="text-white font-mono">{steps}</span>
                            </div>
                            <input type="range" min={5} max={50} step={1} value={steps}
                              onChange={(e) => setSteps(+e.target.value)} className="w-full accent-brand-400 cursor-pointer" />
                            <p className="text-[9px] text-white/25 mt-0.5">More steps = slower but slightly cleaner audio.</p>
                          </div>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>

                {/* Save */}
                <div>
                  <button
                    onClick={handleSave}
                    disabled={createVoice.isPending || !!blocker}
                    className="w-full flex items-center justify-center gap-1.5 rounded-lg bg-brand-400 hover:bg-brand-300 text-white text-xs font-semibold h-9 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    {createVoice.isPending ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
                    {createVoice.isPending ? 'Saving…' : 'Save Voice'}
                  </button>
                  <p className={cn('text-[10px] mt-1.5 text-center', blocker ? 'text-amber-400/70' : 'text-white/25')}>
                    {blocker ?? 'Saved voices appear below and on speakers/segments.'}
                  </p>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Preview line ────────────────────────────────────── */}
        <div
          className="rounded-xl border p-2.5"
          style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}
        >
          <div className="flex items-center justify-between mb-1">
            <label htmlFor="preview-line" className="text-[10px] font-semibold text-white/50 uppercase tracking-wider">
              Preview line
            </label>
            {previewText !== PREVIEW_DEFAULT && (
              <button
                onClick={() => setPreviewText(PREVIEW_DEFAULT)}
                className="text-[9.5px] text-white/35 hover:text-white inline-flex items-center gap-1 transition-colors"
              >
                <RotateCcw size={9} /> Reset
              </button>
            )}
          </div>
          <input
            id="preview-line"
            value={previewText}
            onChange={(e) => setPreviewText(e.target.value)}
            placeholder="Type a line to hear…"
            className="w-full rounded-lg bg-ink-950/60 border border-white/10 px-2.5 h-8 text-xs text-white placeholder-white/25 focus:outline-none focus:border-brand-400 transition-all"
          />
          <p className="text-[9.5px] text-white/30 mt-1">Voices without a sample can make one saying this line (✦). Once a voice has a sample, ▶ plays it straight away.</p>
        </div>

        {/* ── Search + filter ─────────────────────────────────── */}
        <div className="space-y-1.5">
          <div className="relative flex items-center">
            <Search size={11} className="absolute left-2 text-ink-600 pointer-events-none" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search voices…"
              aria-label="Search voices"
              className="w-full h-7 pr-7 rounded border bg-ink-950/60 border-ink-800 text-[11px] text-ink-200 placeholder-ink-600 focus:outline-none focus:border-brand-500/50"
              style={{ paddingLeft: 26 }}
            />
            {search && (
              <button
                onClick={() => setSearch('')}
                aria-label="Clear search"
                className="absolute right-1.5 h-4 w-4 rounded flex items-center justify-center text-white/35 hover:text-white hover:bg-white/10"
              >
                <X size={10} />
              </button>
            )}
          </div>
          <div className="flex items-center gap-1 flex-wrap">
            {(['all', 'design', 'clone', 'ultimate'] as ModeFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setModeFilter(f)}
                className={cn(
                  'px-2 h-6 rounded-md text-[10px] font-medium capitalize transition-colors',
                  modeFilter === f ? 'bg-brand/20 text-brand-200' : 'text-white/40 hover:text-white/80 hover:bg-white/6'
                )}
              >
                {f} <span className="opacity-50">{counts[f]}</span>
              </button>
            ))}
          </div>
        </div>

        {/* ── List ────────────────────────────────────────────── */}
        {isLoading ? (
          <div className="space-y-1.5">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-16 rounded-lg bg-ink-800/40 animate-pulse" style={{ animationDelay: `${i * 90}ms` }} />
            ))}
          </div>
        ) : voiceList.length === 0 ? (
          <div className="rounded-xl border border-dashed border-white/10 py-8 text-center">
            <Mic size={16} className="text-white/25 mx-auto mb-2" />
            <p className="text-[11px] font-semibold text-white/70">No voices yet</p>
            <p className="text-[10px] text-white/35 mt-0.5 max-w-[200px] mx-auto leading-relaxed">
              Create your first one with the New button above.
            </p>
          </div>
        ) : visibleVoices.length === 0 ? (
          <div className="rounded-xl border border-dashed border-white/10 py-6 text-center">
            <p className="text-[11px] text-white/60">No voices match “{search}”</p>
            <button
              onClick={() => { setSearch(''); setModeFilter('all') }}
              className="text-[10px] text-brand-300 hover:text-brand-200 mt-1.5 transition-colors"
            >
              Clear filters
            </button>
          </div>
        ) : (
          <div>
            {groups.map((group) => (
              <VoiceSection
                key={group.key}
                label={group.label}
                hint={group.hint}
                count={group.voices.length}
                open={searching || openSections.has(group.key)}
                onToggle={searching ? undefined : () => toggleSection(group.key)}
              >
                {group.voices.map((v) => (v.builtin ? builtinRow(v) : ownCard(v)))}
              </VoiceSection>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
