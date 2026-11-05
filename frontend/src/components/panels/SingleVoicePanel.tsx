// src/components/panels/SingleVoicePanel.tsx
// Voice tab of a single-voice dub project (0.5.7; it replaces Speakers there — there are no speakers): the one Edge
// voice that reads every line, its speed, and whether an upload goes straight to a finished export. A new voice or
// speed marks the lines voiced so far to be voiced again by the next Generate voices & export (backend PATCH).
import { AudioLines } from 'lucide-react'
import { toast } from 'sonner'
import { useUpdateProject } from '@/hooks/useApi'
import { SINGLE_VOICES } from '@/types'
import type { Project, ProjectUpdate, SingleVoiceName } from '@/types'
import { PanelHeader } from './PanelShell'

const fieldClass =
  'w-full bg-ink-950 border border-ink-700/60 rounded px-2 py-1.5 text-[11px] text-ink-100 ' +
  'focus:outline-none focus:border-brand-500/60 focus:ring-1 focus:ring-brand-500/40'

const SPEEDS = [-20, -10, 0, 10, 20, 30]

export function SingleVoicePanel({ project }: { project: Project }) {
  const update = useUpdateProject()

  const save = (data: ProjectUpdate, done: string) => {
    update.mutate({ id: project.id, data }, {
      onSuccess: () => toast.success(done),
      onError: (e) => toast.error((e as Error).message || "Couldn't save"),
    })
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      <PanelHeader icon={AudioLines} title="Voice" />
      <div className="p-3 space-y-4 overflow-y-auto">
        <p className="text-[11px] leading-snug text-ink-400">
          One voice reads every line of this project. There are no speakers to check.
        </p>
        <label className="block space-y-1">
          <span className="text-[10.5px] font-semibold text-ink-300">Voice</span>
          <select
            className={fieldClass}
            value={project.voice_name}
            disabled={update.isPending}
            onChange={(e) => save({ voice_name: e.target.value as SingleVoiceName },
              'Voice changed. Generate voices & export will make the voices again.')}
          >
            {SINGLE_VOICES.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
          </select>
        </label>
        <label className="block space-y-1">
          <span className="text-[10.5px] font-semibold text-ink-300">Speed</span>
          <select
            className={fieldClass}
            value={String(project.voice_rate)}
            disabled={update.isPending}
            onChange={(e) => save({ voice_rate: Number(e.target.value) },
              'Speed changed. Generate voices & export will make the voices again.')}
          >
            {SPEEDS.map((r) => <option key={r} value={r}>{r === 0 ? 'Normal' : `${r > 0 ? '+' : ''}${r}%`}</option>)}
          </select>
          <span className="block text-[10px] leading-snug text-ink-600">
            One line's speed or volume can still be changed on the timeline, as in any project.
          </span>
        </label>
        <label className="flex items-start gap-2 text-[11px] text-ink-200">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={project.auto_export}
            disabled={update.isPending}
            onChange={(e) => save({ auto_export: e.target.checked },
              e.target.checked ? 'Fully automatic: new uploads export without stopping.' : 'New uploads stop so you can check the Khmer.')}
          />
          <span>
            Fully automatic
            <span className="block text-[10px] leading-snug text-ink-600">
              New uploads go straight to a finished export, without stopping to check the Khmer.
            </span>
          </span>
        </label>
        <p className="text-[10px] leading-snug text-ink-600">
          The voices come from Microsoft Edge's free online voice service, which Microsoft may change or stop.
        </p>
      </div>
    </div>
  )
}
