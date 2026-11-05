// src/features/projects/CreateProjectModal.tsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { PlusCircle } from 'lucide-react'
import { Modal, Field, InputField, SelectField } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'
import { LANGUAGE_OPTIONS, SINGLE_VOICES } from '@/types'
import type { DubMode, SingleVoiceName } from '@/types'
import { useCreateProject } from '@/hooks/useApi'

interface CreateProjectModalProps {
  open: boolean
  onClose: () => void
}

export function CreateProjectModal({ open, onClose }: CreateProjectModalProps) {
  const navigate = useNavigate()
  const { mutate: createProject, isPending } = useCreateProject()

  // Khmer is "km" — the code the backend's LANG_NAMES/GOOGLE_LANG_MAP and
  // LANGUAGE_OPTIONS all use. A "kh" here matched no <option>, so the select
  // silently displayed Chinese while the project was created with an unknown
  // target language that the translator could not resolve.
  const blank = {
    name: '',
    source_lang: 'zh',
    target_lang: 'km',
    story_intro: '',
    dub_mode: 'characters' as DubMode,
    voice_name: 'km-KH-PisethNeural' as SingleVoiceName,
    voice_rate: 0,
    auto_export: false,
  }
  const [form, setForm] = useState(blank)
  const single = form.dub_mode === 'single_voice'
  const [errors, setErrors] = useState<Record<string, string>>({})

  const validate = () => {
    const e: Record<string, string> = {}
    if (!form.name.trim()) e.name = 'Project name is required'
    if (form.source_lang === form.target_lang) {
      e.target_lang = 'Source and target languages must differ'
    }
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate()) return

    createProject(form, {
      onSuccess: (project) => {
        toast.success(`Project "${project.name}" created`)
        onClose()
        setForm(blank)
        navigate(`/projects/${project.id}`)
      },
      onError: () => toast.error('Failed to create project'),
    })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New Project"
      size="md"
    >

      <form onSubmit={handleSubmit} className="space-y-4">
        <InputField
          label="Project Name"
          required
          placeholder="e.g. My Movie Dub — Season 1"
          value={form.name}
          error={errors.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          autoFocus
        />

        <div className="grid grid-cols-2 gap-3">
          <SelectField
            label="Source Language"
            required
            options={LANGUAGE_OPTIONS}
            value={form.source_lang}
            onChange={(e) => setForm((f) => ({ ...f, source_lang: e.target.value }))}
          />
          <SelectField
            label="Target Language"
            required
            options={LANGUAGE_OPTIONS}
            value={form.target_lang}
            error={errors.target_lang}
            onChange={(e) => setForm((f) => ({ ...f, target_lang: e.target.value }))}
          />
        </div>

        {/* Single-voice dub (0.5.7): its own pipeline — one Edge voice, no speaker work */}
        <SelectField
          label="Kind of project"
          options={[
            { value: 'characters', label: 'Dub each character (voices per character)' },
            { value: 'single_voice', label: 'One voice for everyone (fast, no speaker work)' },
          ]}
          value={form.dub_mode}
          onChange={(e) => setForm((f) => ({ ...f, dub_mode: e.target.value as DubMode }))}
        />
        {single && (
          <div className="space-y-3 rounded-lg p-3" style={{ background: 'var(--glass-input-bg)' }}>
            <div className="grid grid-cols-2 gap-3">
              <SelectField
                label="Voice"
                options={SINGLE_VOICES}
                value={form.voice_name}
                onChange={(e) => setForm((f) => ({ ...f, voice_name: e.target.value as SingleVoiceName }))}
              />
              <SelectField
                label="Speed"
                options={[-20, -10, 0, 10, 20, 30].map((r) => ({ value: String(r), label: r === 0 ? 'Normal' : `${r > 0 ? '+' : ''}${r}%` }))}
                value={String(form.voice_rate)}
                onChange={(e) => setForm((f) => ({ ...f, voice_rate: Number(e.target.value) }))}
              />
            </div>
            <label className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-primary)' }}>
              <input
                type="checkbox"
                checked={form.auto_export}
                onChange={(e) => setForm((f) => ({ ...f, auto_export: e.target.checked }))}
              />
              Fully automatic — export without stopping to check the Khmer
            </label>
            <p className="text-[11px] leading-snug" style={{ color: 'var(--color-text-secondary)' }}>
              Every line is translated as natural, easy Khmer and read by one voice. The voices come from
              Microsoft Edge's free online voice service, which Microsoft may change or stop.
            </p>
          </div>
        )}

        {/* Starts the project's story notes (Story tab), which translation follows */}
        <Field label="About this drama">
          <textarea
            className="glass-input w-full rounded-lg px-3 py-2 text-sm focus:outline-none transition-all duration-150 resize-y"
            style={{ background: 'var(--glass-input-bg)', borderColor: 'var(--color-border)', color: 'var(--color-text-primary)', fontFamily: 'var(--font-sans)' }}
            rows={4}
            maxLength={4000}
            placeholder="The story, the main characters and how they talk. Optional — you can add it later in Story."
            value={form.story_intro}
            onChange={(e) => setForm((f) => ({ ...f, story_intro: e.target.value }))}
          />
        </Field>

        <div className="pt-2 flex gap-3">
          <Button
            type="button"
            variant="ghost"
            className="flex-1"
            onClick={onClose}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="default"
            className="flex-1"
            loading={isPending}
            icon={<PlusCircle size={15} />}
          >
            Create Project
          </Button>
        </div>
      </form>
    </Modal>
  )
}
