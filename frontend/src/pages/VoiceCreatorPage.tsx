// src/pages/VoiceCreatorPage.tsx
// Voice Creator — build, preview, name and save reusable VoxCPM2 voices.
import { useState, useRef } from 'react'
import { Mic, Upload, Play, Loader2, Trash2, Plus, Wand2, Copy, Music, Dices } from 'lucide-react'
import { toast } from 'sonner'
import { useVoices, useCreateVoice, useDeleteVoice, usePreviewVoice, useUpdateVoice } from '@/hooks/useApi'
import type { Voice, VoiceMode } from '@/types'
import { cn } from '@/lib/utils'

const MODES: { value: VoiceMode; label: string; blurb: string }[] = [
  { value: 'design',   label: 'Voice Design',        blurb: 'Create a brand-new voice from a description alone. No reference audio.' },
  { value: 'clone',    label: 'Controllable Cloning', blurb: 'Clone a voice from a clip, with optional style guidance.' },
  { value: 'ultimate', label: 'Ultimate Cloning',     blurb: 'Reference clip + its transcript for maximum-fidelity reproduction.' },
]

const PREVIEW_DEFAULT = 'សួស្តី នេះគឺជាសំឡេងសាកល្បងពី Dubify Studio។'

export default function VoiceCreatorPage() {
  const { data: voiceList = [], isLoading } = useVoices()
  const createVoice = useCreateVoice()
  const deleteVoice = useDeleteVoice()
  const previewVoice = usePreviewVoice()
  const updateVoice = useUpdateVoice()

  const [mode, setMode] = useState<VoiceMode>('design')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [transcript, setTranscript] = useState('')
  const [cfg, setCfg] = useState(2.0)
  const [steps, setSteps] = useState(10)
  const [refFile, setRefFile] = useState<File | null>(null)
  const [previewText, setPreviewText] = useState(PREVIEW_DEFAULT)
  const [previewingId, setPreviewingId] = useState<string | null>(null)
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({})
  const fileInputRef = useRef<HTMLInputElement>(null)

  const needsRef = mode === 'clone' || mode === 'ultimate'
  const needsTranscript = mode === 'ultimate'

  const resetForm = () => {
    setName(''); setDescription(''); setTranscript(''); setCfg(2.0); setSteps(10)
    setRefFile(null); setMode('design')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleSave = async () => {
    if (!name.trim()) return toast.error('Give the voice a name')
    if (needsRef && !refFile) return toast.error('This mode needs a reference audio clip')
    if (needsTranscript && !transcript.trim()) return toast.error('Ultimate cloning needs the reference transcript')
    try {
      await createVoice.mutateAsync({
        name: name.trim(), mode, description, reference_transcript: transcript,
        cfg_value: cfg, inference_timesteps: steps, reference_audio: refFile,
      })
      toast.success(`Voice "${name}" saved`)
      resetForm()
    } catch (e: any) {
      toast.error(e?.message || 'Failed to save voice')
    }
  }

  const handlePreview = async (voice: Voice) => {
    setPreviewingId(voice.id)
    toast.info('Generating… first run cold-starts the GPU (~2–3 min)', { duration: 8000 })
    try {
      const blob = await previewVoice.mutateAsync({ id: voice.id, text: previewText })
      const url = URL.createObjectURL(blob)
      // Render a visible <audio> player (autoplay is blocked after the long
      // await, so the user presses play on the control that appears).
      setPreviewUrls((prev) => ({ ...prev, [voice.id]: url }))
      toast.success('Ready — press play ▶')
    } catch (e: any) {
      toast.error(e?.message || 'Preview failed (first run cold-starts the GPU — try again)')
    } finally {
      setPreviewingId(null)
    }
  }

  return (
    <div className="h-full overflow-y-auto" style={{ background: 'var(--color-surface-1)' }}>
      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="flex items-center gap-2 mb-6">
          <Mic size={18} className="text-brand-400" />
          <h1 className="text-lg font-bold text-white">Voice Creator</h1>
          <span className="text-xs text-white/40">— build reusable voices, then pick them in the editor</span>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* ── Create form ──────────────────────────────── */}
          <div className="rounded-lg border p-4 space-y-4" style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}>
            <div className="flex items-center gap-2 text-sm font-semibold text-white"><Wand2 size={14} className="text-brand-400" /> New Voice</div>

            {/* Mode */}
            <div className="grid grid-cols-3 gap-1.5">
              {MODES.map((m) => (
                <button
                  key={m.value}
                  onClick={() => setMode(m.value)}
                  className={cn(
                    'rounded-md border px-2 py-2 text-[11px] font-medium transition-colors text-left',
                    mode === m.value ? 'border-brand-400 bg-brand/15 text-white' : 'border-white/10 text-white/55 hover:text-white hover:bg-white/5'
                  )}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-white/40 -mt-2">{MODES.find((m) => m.value === mode)?.blurb}</p>

            {/* Name */}
            <input
              value={name} onChange={(e) => setName(e.target.value)}
              placeholder="Voice name — e.g. Hero Male Deep"
              className="w-full rounded-md bg-zinc-900 border border-white/10 px-3 py-2 text-sm text-white placeholder-white/30 focus:outline-none focus:border-brand-400"
            />

            {/* Reference audio (clone/ultimate) */}
            {needsRef && (
              <div>
                <label className="text-[11px] text-white/50 mb-1 block">Reference Audio {needsRef && <span className="text-red-400">*</span>}</label>
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="w-full flex items-center justify-center gap-2 rounded-md border border-dashed border-white/15 py-3 text-xs text-white/50 hover:text-white hover:border-brand-400 transition-colors"
                >
                  <Upload size={13} /> {refFile ? refFile.name : 'Click to upload .wav / .mp3'}
                </button>
                <input ref={fileInputRef} type="file" accept="audio/*" className="hidden"
                  onChange={(e) => setRefFile(e.target.files?.[0] || null)} />
              </div>
            )}

            {/* Transcript (ultimate) */}
            {needsTranscript && (
              <div>
                <label className="text-[11px] text-white/50 mb-1 block">Reference Transcript <span className="text-red-400">*</span></label>
                <textarea
                  value={transcript} onChange={(e) => setTranscript(e.target.value)}
                  placeholder="Exact transcript of the reference audio"
                  rows={2}
                  className="w-full rounded-md bg-zinc-900 border border-white/10 px-3 py-2 text-sm text-white placeholder-white/30 focus:outline-none focus:border-brand-400 resize-none"
                />
              </div>
            )}

            {/* Description / control instruction */}
            <div>
              <label className="text-[11px] text-white/50 mb-1 block">
                {mode === 'design' ? 'Voice Description' : 'Control Instruction (optional)'}
              </label>
              <textarea
                value={description} onChange={(e) => setDescription(e.target.value)}
                placeholder={mode === 'design'
                  ? 'A young woman, gentle and sweet voice, speaks slowly'
                  : 'e.g. cheerful tone, slightly faster pace'}
                rows={2}
                className="w-full rounded-md bg-zinc-900 border border-white/10 px-3 py-2 text-sm text-white placeholder-white/30 focus:outline-none focus:border-brand-400 resize-none"
              />
            </div>

            {/* Advanced: cfg + steps */}
            <div className="grid grid-cols-2 gap-3">
              <label className="text-[11px] text-white/50">CFG Scale: <span className="text-white">{cfg.toFixed(1)}</span>
                <input type="range" min={0.5} max={5} step={0.1} value={cfg} onChange={(e) => setCfg(+e.target.value)} className="w-full accent-brand-400" />
              </label>
              <label className="text-[11px] text-white/50">Steps: <span className="text-white">{steps}</span>
                <input type="range" min={5} max={50} step={1} value={steps} onChange={(e) => setSteps(+e.target.value)} className="w-full accent-brand-400" />
              </label>
            </div>

            <button
              onClick={handleSave}
              disabled={createVoice.isPending}
              className="w-full flex items-center justify-center gap-2 rounded-md bg-brand-400 hover:bg-brand-400/90 text-white text-sm font-semibold py-2.5 disabled:opacity-50 transition-colors"
            >
              {createVoice.isPending ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              Save Voice
            </button>
          </div>

          {/* ── Saved voices ─────────────────────────────── */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-semibold text-white"><Music size={14} className="text-brand-400" /> Your Voices ({voiceList.length})</div>
            </div>

            {/* Preview text shared across voices */}
            <input
              value={previewText} onChange={(e) => setPreviewText(e.target.value)}
              placeholder="Preview text"
              className="w-full rounded-md bg-zinc-900 border border-white/10 px-3 py-1.5 text-xs text-white placeholder-white/30 focus:outline-none focus:border-brand-400"
            />

            {isLoading ? (
              <div className="text-xs text-white/40 py-8 text-center">Loading…</div>
            ) : voiceList.length === 0 ? (
              <div className="text-xs text-white/40 py-8 text-center border border-dashed border-white/10 rounded-lg">No voices yet — create one on the left.</div>
            ) : (
              <div className="space-y-2">
                {voiceList.map((v) => (
                  <div key={v.id} className="rounded-md border p-3" style={{ borderColor: 'var(--color-border)', background: 'var(--color-surface-3)' }}>
                    <div className="flex items-center gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-white truncate">{v.name}</span>
                          <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-brand/15 text-brand-400 border border-brand-400/20 uppercase">{v.mode}</span>
                        </div>
                        {v.description && <div className="text-[11px] text-white/40 truncate">{v.description}</div>}
                      </div>
                      <button
                        onClick={() => handlePreview(v)}
                        disabled={previewingId !== null}
                        title="Generate preview"
                        className="shrink-0 flex items-center gap-1 px-2 h-7 rounded-md bg-brand-400/90 hover:bg-brand-400 text-white text-[11px] font-medium disabled:opacity-40"
                      >
                        {previewingId === v.id ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                        {previewingId === v.id ? 'Generating…' : 'Preview'}
                      </button>
                      <button
                        onClick={() => {
                          const newSeed = Math.floor(Math.random() * 2_000_000_000) + 1
                          updateVoice.mutate(
                            { id: v.id, data: { seed: newSeed } as any },
                            { onSuccess: () => { setPreviewUrls((p) => { const n = { ...p }; delete n[v.id]; return n }); toast.success('New voice rolled — preview to hear it') } }
                          )
                        }}
                        title="Reroll voice (new random identity, stays consistent)"
                        className="shrink-0 flex items-center justify-center h-7 w-7 rounded-md bg-white/5 hover:bg-white/10 text-white/50"
                      >
                        <Dices size={13} />
                      </button>
                      <button
                        onClick={() => { navigator.clipboard.writeText(v.id); toast.success('Voice ID copied') }}
                        title="Copy ID"
                        className="shrink-0 flex items-center justify-center h-7 w-7 rounded-md bg-white/5 hover:bg-white/10 text-white/50"
                      >
                        <Copy size={12} />
                      </button>
                      <button
                        onClick={() => { if (confirm(`Delete voice "${v.name}"?`)) deleteVoice.mutate(v.id) }}
                        title="Delete"
                        className="shrink-0 flex items-center justify-center h-7 w-7 rounded-md bg-white/5 hover:bg-red-500/20 text-white/50 hover:text-red-400"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                    {previewUrls[v.id] && (
                      <audio
                        controls
                        autoPlay
                        src={previewUrls[v.id]}
                        className="w-full mt-2 h-9"
                      />
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
