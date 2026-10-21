// src/components/panels/SettingsPanel.tsx
// Settings tab of the dock — AI service keys, storage, theme and about.
// In the desktop app keys are saved to the user's own settings file (never
// shown back); during development they come from backend/.env (read-only here).
import { useState } from 'react'
import { Sun, Moon, Settings2, KeyRound, HardDrive, CheckCircle2, AlertTriangle } from 'lucide-react'
import { toast } from 'sonner'
import { useThemeStore } from '@/store/themeStore'
import { useAppSettings, useGeminiModels, useTestGeminiModel, useUpdateAppSettings } from '@/hooks/useApi'
import { cn } from '@/lib/utils'
import type { AppSettings, AppSettingsUpdate, AppSettingsValues, GeminiModelChoice, GeminiModelTestResult } from '@/types'
import { PanelHeader } from './PanelShell'
import { MoveloStudioIcon } from '@/components/brand/Movelo'

const inputClass =
  'w-full bg-ink-950 text-[11px] text-ink-200 rounded border border-ink-700/60 px-2 py-1 ' +
  'focus:outline-none focus:border-brand-500/60 disabled:opacity-60 placeholder:text-ink-600'

function SecretField({
  label, isSet, value, onChange, onRemove, disabled, hint,
}: {
  label: string; isSet: boolean; value: string; onChange: (v: string) => void
  onRemove: () => void; disabled: boolean; hint: string
}) {
  return (
    <label className="block space-y-1">
      <span className="flex items-center justify-between text-[10px] text-ink-400">
        {label}
        {isSet ? (
          <span className="flex items-center gap-1.5">
            <span className="text-emerald-400/80">Saved</span>
            {!disabled && (
              <button type="button" onClick={onRemove} className="text-ink-500 hover:text-red-400 transition-colors">
                Remove
              </button>
            )}
          </span>
        ) : (
          <span className="text-amber-400/80">Not set</span>
        )}
      </span>
      <input
        type="password"
        autoComplete="off"
        spellCheck={false}
        className={inputClass}
        placeholder={isSet ? '•••••••• (enter a new value to replace)' : hint}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
    </label>
  )
}

/**
 * One dropdown per AI job, filled with the models the saved key can use.
 * Picking a model runs a tiny real request (a listed model can still refuse to
 * work); "Automatic" keeps the built-in choice, which replaces itself when
 * Google retires it.
 */
function GeminiModelsField({
  hasKey, disabled, value, onChange,
}: {
  hasKey: boolean; disabled: boolean
  value: (choice: GeminiModelChoice) => string
  onChange: (choice: GeminiModelChoice, model: string) => void
}) {
  const models = useGeminiModels(hasKey)
  const test = useTestGeminiModel()
  const [results, setResults] = useState<Record<string, GeminiModelTestResult | 'testing'>>({})

  if (!hasKey) {
    return <p className="text-[9.5px] text-ink-500 leading-snug">Save your Gemini API key to choose the AI models.</p>
  }
  if (models.isLoading) return <p className="text-[9.5px] text-ink-500">Loading the models your key can use…</p>
  if (models.isError || !models.data) {
    return (
      <p className="text-[9.5px] text-amber-300/90 leading-snug">
        {(models.error as Error)?.message || "Couldn't load the model list."}{' '}
        <button type="button" onClick={() => models.refetch()} className="underline hover:text-amber-200">Try again</button>
      </p>
    )
  }

  const pick = (choice: GeminiModelChoice, model: string) => {
    onChange(choice, model)
    if (!model) {
      setResults((r) => { const next = { ...r }; delete next[choice.setting]; return next })
      return
    }
    setResults((r) => ({ ...r, [choice.setting]: 'testing' }))
    test.mutate({ setting: choice.setting, model }, {
      onSuccess: (result) => setResults((r) => ({ ...r, [choice.setting]: result })),
      onError: (e) => setResults((r) => ({ ...r, [choice.setting]: { ok: false, message: (e as Error).message } })),
    })
  }

  return (
    <div className="space-y-2">
      {models.data.map((choice) => {
        const selected = value(choice)
        const configured = choice.value || choice.automatic
        const replaced = choice.in_use !== configured
        const result = results[choice.setting]
        const options = choice.value && !choice.options.includes(choice.value)
          ? [choice.value, ...choice.options] : choice.options
        return (
          <label key={choice.setting} className="block space-y-1">
            <span className="text-[10px] text-ink-400">{choice.label}</span>
            <select
              className={inputClass} disabled={disabled}
              value={selected}
              onChange={(e) => pick(choice, e.target.value)}
            >
              <option value="">Automatic (recommended) — {choice.automatic}</option>
              {options.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            {result === 'testing' && <span className="block text-[9.5px] text-ink-500">Checking {selected}…</span>}
            {result && result !== 'testing' && (
              <span className={cn('flex items-start gap-1 text-[9.5px] leading-snug',
                result.ok ? 'text-emerald-400/90' : 'text-amber-300/90')}>
                {result.ok ? <CheckCircle2 size={10} className="shrink-0 mt-px" /> : <AlertTriangle size={10} className="shrink-0 mt-px" />}
                {result.message}
              </span>
            )}
            {replaced && !result && (
              <span className="flex items-start gap-1 text-[9.5px] text-amber-300/90 leading-snug">
                <AlertTriangle size={10} className="shrink-0 mt-px" />
                {configured} isn't available any more — using {choice.in_use}. Choose a model to make it permanent.
              </span>
            )}
          </label>
        )
      })}
    </div>
  )
}

function AiServicesSection({ data }: { data: AppSettings }) {
  const update = useUpdateAppSettings()
  const [secrets, setSecrets] = useState({ GEMINI_API_KEY: '', HF_TOKEN: '' })
  // Only fields the user touched are sent; untouched ones show the saved value.
  const [edits, setEdits] = useState<Partial<AppSettingsValues>>({})
  const disabled = !data.editable || update.isPending
  const value = <K extends keyof AppSettingsValues>(key: K): AppSettingsValues[K] =>
    (edits[key] ?? data.values[key]) as AppSettingsValues[K]
  const edit = <K extends keyof AppSettingsValues>(key: K, v: AppSettingsValues[K]) =>
    setEdits((prev) => ({ ...prev, [key]: v }))

  const send = (payload: AppSettingsUpdate, message: string) => {
    update.mutate(payload, {
      onSuccess: () => {
        setSecrets({ GEMINI_API_KEY: '', HF_TOKEN: '' })
        setEdits({})
        toast.success(message)
      },
      onError: (e) => toast.error((e as Error).message || 'Could not save settings'),
    })
  }
  const save = () => {
    const payload: AppSettingsUpdate = { ...edits }
    if (secrets.GEMINI_API_KEY.trim()) payload.GEMINI_API_KEY = secrets.GEMINI_API_KEY.trim()
    if (secrets.HF_TOKEN.trim()) payload.HF_TOKEN = secrets.HF_TOKEN.trim()
    if (Object.keys(payload).length === 0) return
    send(payload, 'Settings saved')
  }
  const dirty = Object.keys(edits).length > 0 || !!secrets.GEMINI_API_KEY.trim() || !!secrets.HF_TOKEN.trim()

  return (
    <section className="space-y-2">
      <span className="flex items-center gap-1.5 text-[10px] font-semibold text-ink-500 uppercase tracking-wider">
        <KeyRound size={10} /> AI services
      </span>
      {!data.editable && (
        <p className="text-[9.5px] text-ink-500 leading-snug">
          While developing, these come from <span className="font-mono">backend/.env</span> — edit that file and
          restart the backend.
        </p>
      )}
      <div className="space-y-2.5 rounded-xl border border-ink-800/60 bg-ink-800/20 p-3">
        <SecretField
          label="Gemini API key" isSet={data.secrets.GEMINI_API_KEY} disabled={disabled}
          value={secrets.GEMINI_API_KEY} hint="Needed for transcription, speakers and translation"
          onChange={(v) => setSecrets((s) => ({ ...s, GEMINI_API_KEY: v }))}
          onRemove={() => send({ GEMINI_API_KEY: '' }, 'Gemini API key removed')}
        />
        <SecretField
          label="Hugging Face token" isSet={data.secrets.HF_TOKEN} disabled={disabled}
          value={secrets.HF_TOKEN} hint="Optional — higher limits for separation"
          onChange={(v) => setSecrets((s) => ({ ...s, HF_TOKEN: v }))}
          onRemove={() => send({ HF_TOKEN: '' }, 'Hugging Face token removed')}
        />
        <label className="block space-y-1">
          <span className="text-[10px] text-ink-400">VoxCPM voice server (Gradio URL)</span>
          <input
            className={inputClass} disabled={disabled} spellCheck={false}
            placeholder="Blank = free public HF Space"
            value={value('VOXCPM2_API_URL')}
            onChange={(e) => edit('VOXCPM2_API_URL', e.target.value)}
          />
        </label>
        <label className="block space-y-1">
          <span className="text-[10px] text-ink-400">Translation engine</span>
          <select
            className={inputClass} disabled={disabled}
            value={value('TRANSLATION_BACKEND')}
            onChange={(e) => edit('TRANSLATION_BACKEND', e.target.value as AppSettingsValues['TRANSLATION_BACKEND'])}
          >
            <option value="gemini">Gemini (uses scene context — recommended)</option>
            <option value="deep">Google Translate (free, no context)</option>
          </select>
        </label>
        <div className="space-y-1.5 pt-1">
          <span className="text-[10px] font-medium text-ink-300">AI models</span>
          <GeminiModelsField
            hasKey={data.secrets.GEMINI_API_KEY}
            disabled={disabled}
            value={(choice) => (edits[choice.setting] ?? choice.value) as string}
            onChange={(choice, model) => edit(choice.setting, model)}
          />
        </div>
        <details className="group">
          <summary className="cursor-pointer text-[10px] text-ink-500 hover:text-ink-300 select-none">Advanced</summary>
          <div className="space-y-2 pt-2">
            {([
              ['VOXCPM2_HF_SPACE_FALLBACK', 'Fallback voice Space'],
            ] as const).map(([key, label]) => (
              <label key={key} className="block space-y-1">
                <span className="text-[10px] text-ink-400">{label}</span>
                <input
                  className={inputClass} disabled={disabled} spellCheck={false}
                  value={value(key)} onChange={(e) => edit(key, e.target.value)}
                />
              </label>
            ))}
            <label className="block space-y-1">
              <span className="text-[10px] text-ink-400">Voices generated at once (1–8)</span>
              <input
                type="number" min={1} max={8} className={inputClass} disabled={disabled}
                value={value('TTS_BATCH_CONCURRENCY')}
                onChange={(e) => edit('TTS_BATCH_CONCURRENCY', Math.min(8, Math.max(1, Number(e.target.value) || 1)))}
              />
            </label>
          </div>
        </details>
        {data.editable && (
          <button
            onClick={save}
            disabled={!dirty || update.isPending}
            className="w-full py-1 rounded bg-brand-600 hover:bg-brand-500 disabled:opacity-40 text-white text-[10.5px] font-semibold transition-colors"
          >
            {update.isPending ? 'Saving…' : 'Save'}
          </button>
        )}
      </div>
    </section>
  )
}

function StorageSection({ data }: { data: AppSettings }) {
  const row = (label: string, text: string) => (
    <div className="space-y-0.5">
      <span className="text-[9.5px] text-ink-500">{label}</span>
      <p className="text-[10px] font-mono text-ink-300 break-all select-text">{text}</p>
    </div>
  )
  return (
    <section className="space-y-2">
      <span className="flex items-center gap-1.5 text-[10px] font-semibold text-ink-500 uppercase tracking-wider">
        <HardDrive size={10} /> Storage
      </span>
      <div className="space-y-2 rounded-xl border border-ink-800/60 bg-ink-800/20 p-3">
        {row('Database', data.storage.database === 'local' ? 'On this Mac' : 'Server database (DATABASE_URL)')}
        {row('Videos and voices', data.storage.upload_dir)}
        {data.storage.log_dir && row('Logs', data.storage.log_dir)}
        <div className="flex items-start gap-1.5 pt-0.5">
          {data.ffmpeg && data.ffprobe ? (
            <>
              <CheckCircle2 size={11} className="text-emerald-400 shrink-0 mt-px" />
              <p className="text-[10px] text-ink-400 break-all">ffmpeg found: <span className="font-mono">{data.ffmpeg}</span></p>
            </>
          ) : (
            <>
              <AlertTriangle size={11} className="text-amber-400 shrink-0 mt-px" />
              <p className="text-[10px] text-amber-300/90 leading-snug">
                ffmpeg was not found, so videos can't be processed. Install it with{' '}
                <span className="font-mono">brew install ffmpeg</span> and restart the app.
              </p>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

type ThemeOption = 'dark' | 'light'

const THEME_OPTIONS: { value: ThemeOption; icon: typeof Sun; label: string }[] = [
  { value: 'dark',  icon: Moon, label: 'Dark' },
  { value: 'light', icon: Sun,  label: 'Light' },
]

function ThemeCard({
  option, active, onClick,
}: { option: typeof THEME_OPTIONS[0]; active: boolean; onClick: () => void }) {
  const Icon = option.icon
  const isLight = option.value === 'light'

  return (
    <button
      className={cn(
        'relative flex items-center gap-3 p-2.5 rounded-xl border-2 text-left transition-colors duration-150 w-full',
        active
          ? 'border-brand/60 bg-brand/8'
          : 'border-border bg-surface-3 hover:border-border-strong hover:bg-surface-4'
      )}
      onClick={onClick}
      aria-pressed={active}
    >
      {/* Mini theme preview */}
      <div className={cn(
        'h-12 w-16 shrink-0 rounded-lg border overflow-hidden relative',
        isLight ? 'bg-ink-100 border-ink-200' : 'bg-ink-950 border-ink-800'
      )}>
        <div className={cn('absolute left-0 top-0 bottom-0 w-4', isLight ? 'bg-ink-200' : 'bg-ink-900')}>
          <div className={cn('mx-1 mt-1.5 h-1 rounded-full', isLight ? 'bg-ink-400' : 'bg-ink-700')} />
          <div className={cn('mx-1 mt-1 h-1 rounded-full w-2/3', isLight ? 'bg-brand-400/60' : 'bg-brand-600/60')} />
        </div>
        <div className="absolute left-5 right-1 top-1.5 bottom-1.5 flex flex-col gap-1">
          <div className={cn('h-1 rounded-full w-1/2', isLight ? 'bg-ink-300' : 'bg-ink-700')} />
          <div className="flex gap-1 mt-0.5 flex-1">
            {[...Array(2)].map((_, i) => (
              <div key={i} className={cn('flex-1 rounded', isLight ? 'bg-ink-200' : 'bg-ink-800')} />
            ))}
          </div>
        </div>
      </div>

      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <Icon size={12} className={active ? 'text-brand-400' : 'text-white/40'} />
          <p className={cn('text-[12px] font-semibold', active ? 'text-white' : 'text-white/80')}>{option.label}</p>
        </div>
      </div>

      {active && (
        <div className="absolute top-2 right-2 h-4 w-4 rounded-full bg-brand flex items-center justify-center">
          <svg viewBox="0 0 12 12" className="w-2.5 h-2.5 text-white" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="2,6 5,9 10,3" />
          </svg>
        </div>
      )}
    </button>
  )
}

export function SettingsPanel() {
  const { theme, setTheme } = useThemeStore()
  const { data: appSettings } = useAppSettings()

  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader icon={Settings2} title="Settings" />
      <div className="flex-1 overflow-y-auto p-3 space-y-5">
        {appSettings && (
          <>
            <AiServicesSection data={appSettings} />
            <StorageSection data={appSettings} />
            <div className="h-px bg-ink-800/60" />
          </>
        )}

        {/* Appearance */}
        <section className="space-y-2">
          <span className="text-[10px] font-semibold text-ink-500 uppercase tracking-wider">Appearance</span>
          <div className="space-y-2">
            {THEME_OPTIONS.map((option) => (
              <ThemeCard
                key={option.value}
                option={option}
                active={theme === option.value}
                onClick={() => setTheme(option.value)}
              />
            ))}
          </div>
        </section>

        <div className="h-px bg-ink-800/60" />

        {/* About */}
        <section className="space-y-2">
          <span className="text-[10px] font-semibold text-ink-500 uppercase tracking-wider">About</span>
          <div className="rounded-xl border border-ink-800/60 bg-ink-800/20 p-3">
            <div className="flex items-center gap-2 mb-2.5">
              <MoveloStudioIcon size={24} />
              <div>
                <p className="text-[11.5px] font-semibold text-white leading-tight">Movelo Studio</p>
                <p className="text-[9.5px] text-white/40">Video editor with AI dubbing</p>
              </div>
            </div>
            <div className="space-y-1 text-[10px] text-white/40">
              <div className="flex justify-between"><span>Version</span><span className="text-white/60 font-mono">{appSettings?.version ?? '—'}</span></div>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
