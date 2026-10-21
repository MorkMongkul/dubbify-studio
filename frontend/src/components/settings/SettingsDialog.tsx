// src/components/settings/SettingsDialog.tsx
// The Settings window: the gear at the foot of the rail (or ⌘,) opens it over
// the editor, groups on the left and the open group's settings on the right.
// App renders it above every route and makes the rest inert while it's open.
// The theme changes at once; other edits wait in a draft until their group's
// Save (lib/settingsDraft.ts), and closing the window drops what wasn't saved.
// In the desktop app keys are saved to the user's own settings file (never
// shown back); during development they come from backend/.env (read-only here).
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, CheckCircle2, Moon, Sun, X } from 'lucide-react'
import { toast } from 'sonner'
import { useEditorStore } from '@/store/editorStore'
import { useThemeStore } from '@/store/themeStore'
import { useAppSettings, useGeminiModels, useTestGeminiModel, useUpdateAppSettings } from '@/hooks/useApi'
import { groupChanges, type SettingsGroup } from '@/lib/settingsDraft'
import { MoveloStudioIcon } from '@/components/brand/Movelo'
import { cn } from '@/lib/utils'
import type { AppSettings, AppSettingsUpdate, AppSettingsValues, GeminiModelChoice, GeminiModelTestResult } from '@/types'

const GROUPS: { id: SettingsGroup; label: string }[] = [
  { id: 'appearance', label: 'Appearance' },
  { id: 'keys',       label: 'AI keys' },
  { id: 'voices',     label: 'Voices' },
  { id: 'models',     label: 'AI models' },
  { id: 'storage',    label: 'Storage' },
  { id: 'about',      label: 'About' },
]

// A ring as well as the border on focus: the light theme pins the border colour
const fieldClass =
  'w-full h-[30px] rounded-[7px] border border-ink-700/60 bg-ink-950 px-2.5 text-[11.5px] text-ink-100 ' +
  'placeholder:text-ink-600 focus:outline-none focus:border-brand-500/60 focus:ring-1 focus:ring-brand-500/40 ' +
  'disabled:opacity-60'

const line = { borderColor: 'var(--color-border)' }

// ⌘Z, ⇧⌘Z or ⌘Y
const isUndo = (e: { key: string; metaKey: boolean; ctrlKey: boolean }) =>
  (e.metaKey || e.ctrlKey) && (e.key.toLowerCase() === 'z' || e.key.toLowerCase() === 'y')

const inTextBox = (target: EventTarget | null) =>
  target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement

// Closed but still fading out: keys belong to the editor again
const closing = () => !useEditorStore.getState().settingsOpen

/** One setting: its name on the left, its box on the right. */
function Row({ label, status, children }: { label: string; status?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3.5 py-3 border-b" style={line}>
      <div className="w-[150px] shrink-0 pt-[7px] text-[12px] leading-snug text-white">
        {label}
        {status && <div className="mt-1.5 flex items-center gap-2">{status}</div>}
      </div>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  )
}

/** Something to read rather than change, level with the row's name. */
function Value({ mono, children }: { mono?: boolean; children: ReactNode }) {
  return (
    <p className={cn('pt-[7px] text-[11px] leading-snug text-ink-300 break-all select-text', mono && 'font-mono')}>
      {children}
    </p>
  )
}

function Note({ tone, children }: { tone?: 'ok' | 'warn'; children: ReactNode }) {
  const Icon = tone === 'ok' ? CheckCircle2 : AlertTriangle
  return (
    <p className={cn('flex items-start gap-1 pt-1.5 text-[10.5px] leading-snug',
      tone === 'ok' ? 'text-emerald-400/90' : tone === 'warn' ? 'text-amber-300/90' : 'text-ink-500')}>
      {tone && <Icon size={11} className="shrink-0 mt-px" />}
      <span>{children}</span>
    </p>
  )
}

function Pill({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <span className={cn('rounded-full px-[7px] py-px text-[10px]',
      ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-amber-400/10 text-amber-400')}>
      {children}
    </span>
  )
}

function SaveButton({ onClick, disabled, saving }: { onClick: () => void; disabled: boolean; saving: boolean }) {
  return (
    <div className="flex justify-end pt-3.5">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled || saving}
        className="px-3.5 py-[7px] rounded-lg bg-brand hover:bg-brand-hover disabled:opacity-40 disabled:hover:bg-brand text-white text-[11.5px] font-semibold transition-colors"
      >
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  )
}

function SecretRow({
  label, isSet, value, onChange, onRemove, disabled, placeholder,
}: {
  label: string; isSet: boolean; value: string; onChange: (v: string) => void
  onRemove: () => void; disabled: boolean; placeholder: string
}) {
  return (
    <Row
      label={label}
      status={
        <>
          <Pill ok={isSet}>{isSet ? 'Saved' : 'Not set'}</Pill>
          {isSet && !disabled && (
            <button type="button" onClick={onRemove} className="text-[10.5px] text-ink-500 hover:text-red-400 transition-colors">
              Remove
            </button>
          )}
        </>
      }
    >
      <input
        type="password"
        autoComplete="off"
        spellCheck={false}
        aria-label={label}
        className={fieldClass}
        placeholder={isSet ? '•••••••• (paste a new one to replace it)' : placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
    </Row>
  )
}

/**
 * One row per AI job, filled with the models the saved key can use. Picking a
 * model runs a tiny real request (a listed model can still refuse to work);
 * "Automatic" keeps the built-in choice, which replaces itself when Google
 * retires it.
 */
function ModelRows({
  hasKey, disabled, value, onChange, onAddKey,
}: {
  hasKey: boolean; disabled: boolean
  value: (choice: GeminiModelChoice) => string
  onChange: (choice: GeminiModelChoice, model: string) => void
  onAddKey: () => void
}) {
  const models = useGeminiModels(hasKey)
  const test = useTestGeminiModel()
  const [results, setResults] = useState<Record<string, GeminiModelTestResult | 'testing'>>({})

  if (!hasKey) {
    return (
      <p className="py-3 text-[11px] text-ink-500">
        Choosing models needs a Gemini API key.{' '}
        <button type="button" onClick={onAddKey} className="text-brand-400 hover:text-brand-300 transition-colors">Add one</button>
      </p>
    )
  }
  if (models.isLoading) return <p className="py-3 text-[11px] text-ink-500">Loading the models your key can use…</p>
  if (models.isError || !models.data) {
    return (
      <Note tone="warn">
        {(models.error as Error)?.message || "Couldn't load the model list."}{' '}
        <button type="button" onClick={() => models.refetch()} className="underline hover:text-amber-200">Try again</button>
      </Note>
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
    <>
      {models.data.map((choice) => {
        const selected = value(choice)
        const configured = choice.value || choice.automatic
        const replaced = choice.in_use !== configured
        const result = results[choice.setting]
        const options = choice.value && !choice.options.includes(choice.value)
          ? [choice.value, ...choice.options] : choice.options
        return (
          <Row key={choice.setting} label={choice.label}>
            <select
              className={fieldClass}
              disabled={disabled}
              aria-label={choice.label}
              value={selected}
              onChange={(e) => pick(choice, e.target.value)}
            >
              <option value="">Automatic — {choice.automatic}</option>
              {options.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            {result === 'testing' && <Note>Checking {selected}…</Note>}
            {result && result !== 'testing' && <Note tone={result.ok ? 'ok' : 'warn'}>{result.message}</Note>}
            {replaced && !result && (
              <Note tone="warn">
                {configured} isn't available any more, so {choice.in_use} is used. Choose a model to keep it.
              </Note>
            )}
          </Row>
        )
      })}
    </>
  )
}

function AppearanceGroup() {
  const { theme, setTheme } = useThemeStore()
  return (
    <Row label="Theme">
      <div className="inline-flex gap-0.5 rounded-lg border border-ink-700/60 p-0.5">
        {([['dark', Moon, 'Dark'], ['light', Sun, 'Light']] as const).map(([option, Icon, label]) => (
          <button
            key={option}
            type="button"
            onClick={() => setTheme(option)}
            aria-pressed={theme === option}
            className={cn('flex items-center gap-1.5 h-6 px-3 rounded-md text-[11.5px] transition-colors',
              theme === option ? 'bg-white/10 text-white' : 'text-ink-400 hover:text-white')}
          >
            <Icon size={12} /> {label}
          </button>
        ))}
      </div>
    </Row>
  )
}

function SettingsWindow({
  group, onGroup, onClose,
}: { group: SettingsGroup; onGroup: (g: SettingsGroup) => void; onClose: () => void }) {
  const { data, isError, error, refetch } = useAppSettings()
  const update = useUpdateAppSettings()
  const [draft, setDraft] = useState<AppSettingsUpdate>({})
  const windowRef = useRef<HTMLDivElement>(null)

  // Focus moves into the window (and back where it was afterwards), and keys
  // stay with it: the editor's shortcuts (Space, Delete, ⌘Z) mustn't act
  // behind it, even when focus has fallen out to the page itself.
  useEffect(() => {
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null
    windowRef.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus()
    const keepKeys = (e: KeyboardEvent) => {
      const win = windowRef.current
      if (!win || win.contains(e.target as Node) || closing()) return
      e.stopPropagation()
      // Out here a key does nothing, but ⌘ shortcuts other than undo still
      // reach the app's menu (⌘Q)
      if (!(e.metaKey || e.ctrlKey) || isUndo(e)) e.preventDefault()
      if (e.key === 'Escape') onClose()
      else win.focus()
    }
    window.addEventListener('keydown', keepKeys, true)
    return () => {
      window.removeEventListener('keydown', keepKeys, true)
      before?.focus({ preventScroll: true })
    }
  }, [onClose])

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (closing()) return
    e.stopPropagation()
    // Out of a text box ⌘Z would reach the system's Undo, which undoes typing
    // in the editor behind the window
    if (isUndo(e) && !inTextBox(e.target)) e.preventDefault()
    if (e.key === 'Escape') onClose()
  }

  // ↑ ↓ move between the groups, like a list
  const onTabKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const at = GROUPS.findIndex((g) => g.id === group)
    const next = GROUPS[(at + step + GROUPS.length) % GROUPS.length].id
    onGroup(next)
    windowRef.current?.querySelector<HTMLElement>(`#settings-tab-${next}`)?.focus()
  }

  const changes = (g: SettingsGroup) => (data ? groupChanges(draft, data.values, g) : {})
  const unsaved = (g: SettingsGroup) => Object.keys(changes(g)).length > 0
  const edit = (patch: AppSettingsUpdate) => setDraft((d) => ({ ...d, ...patch }))
  const forget = (key: keyof AppSettingsUpdate) =>
    setDraft((d) => {
      const next = { ...d }
      delete next[key]
      return next
    })

  const send = (payload: AppSettingsUpdate, message: string) => {
    update.mutate(payload, {
      onSuccess: () => {
        setDraft((d) => {
          const next = { ...d }
          for (const key of Object.keys(payload)) delete next[key as keyof AppSettingsUpdate]
          return next
        })
        toast.success(message)
      },
      onError: (e) => toast.error((e as Error).message || "Couldn't save the settings"),
    })
  }
  const save = (g: SettingsGroup) => {
    const payload = changes(g)
    if (Object.keys(payload).length) send(payload, 'Settings saved')
  }
  // Enter in a box saves its group
  const saveOnEnter = (g: SettingsGroup) => (e: ReactKeyboardEvent) => {
    if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') {
      e.preventDefault()
      save(g)
    }
  }

  const groupBody = (settings: AppSettings): ReactNode => {
    const disabled = !settings.editable || update.isPending
    const value = <K extends keyof AppSettingsValues>(key: K) => (draft[key] ?? settings.values[key]) as AppSettingsValues[K]
    const footer = (g: SettingsGroup) =>
      settings.editable && <SaveButton onClick={() => save(g)} disabled={!unsaved(g)} saving={update.isPending} />
    const devNote = !settings.editable && (
      <p className="-mt-1.5 mb-1 text-[10.5px] leading-snug text-ink-500">
        While developing, these come from <span className="font-mono">backend/.env</span>. Edit that file and restart the backend.
      </p>
    )

    switch (group) {
      case 'keys':
        return (
          <div onKeyDown={saveOnEnter('keys')}>
            {devNote}
            <SecretRow
              label="Gemini API key" isSet={settings.secrets.GEMINI_API_KEY} disabled={disabled}
              placeholder="Paste your key" value={draft.GEMINI_API_KEY ?? ''}
              onChange={(v) => edit({ GEMINI_API_KEY: v })}
              onRemove={() => send({ GEMINI_API_KEY: '' }, 'Gemini API key removed')}
            />
            <SecretRow
              label="Hugging Face token" isSet={settings.secrets.HF_TOKEN} disabled={disabled}
              placeholder="Optional — higher limits for separation" value={draft.HF_TOKEN ?? ''}
              onChange={(v) => edit({ HF_TOKEN: v })}
              onRemove={() => send({ HF_TOKEN: '' }, 'Hugging Face token removed')}
            />
            {footer('keys')}
          </div>
        )
      case 'voices':
        return (
          <div onKeyDown={saveOnEnter('voices')}>
            {devNote}
            <Row label="VoxCPM voice server">
              <input
                className={fieldClass} disabled={disabled} spellCheck={false} aria-label="VoxCPM voice server"
                placeholder="Gradio link (blank = free public Space)"
                value={value('VOXCPM2_API_URL')}
                onChange={(e) => edit({ VOXCPM2_API_URL: e.target.value })}
              />
            </Row>
            <Row label="Fallback voice Space">
              <input
                className={fieldClass} disabled={disabled} spellCheck={false} aria-label="Fallback voice Space"
                value={value('VOXCPM2_HF_SPACE_FALLBACK')}
                onChange={(e) => edit({ VOXCPM2_HF_SPACE_FALLBACK: e.target.value })}
              />
            </Row>
            <Row label="Voices made at once">
              <select
                className={cn(fieldClass, 'w-20')} disabled={disabled} aria-label="Voices made at once"
                value={value('TTS_BATCH_CONCURRENCY')}
                onChange={(e) => edit({ TTS_BATCH_CONCURRENCY: Number(e.target.value) })}
              >
                {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </Row>
            {footer('voices')}
          </div>
        )
      case 'models':
        return (
          <div>
            {devNote}
            <Row label="Translation engine">
              <select
                className={fieldClass} disabled={disabled} aria-label="Translation engine"
                value={value('TRANSLATION_BACKEND')}
                onChange={(e) => edit({ TRANSLATION_BACKEND: e.target.value as AppSettingsValues['TRANSLATION_BACKEND'] })}
              >
                <option value="gemini">Gemini (recommended)</option>
                <option value="deep">Google Translate (free)</option>
              </select>
            </Row>
            <ModelRows
              hasKey={settings.secrets.GEMINI_API_KEY}
              disabled={disabled}
              value={(choice) => (draft[choice.setting] ?? choice.value) as string}
              // Back to the saved choice is no change (the saved setting names
              // the model that "Automatic" stands for)
              onChange={(choice, model) => (model === choice.value ? forget(choice.setting) : edit({ [choice.setting]: model }))}
              onAddKey={() => onGroup('keys')}
            />
            {footer('models')}
          </div>
        )
      case 'storage':
        return (
          <>
            <Row label="Projects">
              <Value>{settings.storage.database === 'local' ? 'On this Mac' : 'Server database (DATABASE_URL)'}</Value>
            </Row>
            <Row label="Videos and voices"><Value mono>{settings.storage.upload_dir}</Value></Row>
            {settings.storage.log_dir && <Row label="Logs"><Value mono>{settings.storage.log_dir}</Value></Row>}
            <Row label="ffmpeg">
              {settings.ffmpeg && settings.ffprobe ? (
                <Value mono>{settings.ffmpeg}</Value>
              ) : (
                <Note tone="warn">
                  Not found, so videos can't be processed. Install it with{' '}
                  <span className="font-mono">brew install ffmpeg</span> and restart the app.
                </Note>
              )}
            </Row>
          </>
        )
      case 'about':
        return (
          <>
            <div className="flex items-center gap-3 pb-4 border-b" style={line}>
              <MoveloStudioIcon size={36} />
              <div>
                <p className="text-[13px] font-semibold text-white">Movelo Studio</p>
                <p className="text-[11px] text-ink-500">Video editor with AI dubbing</p>
              </div>
            </div>
            <Row label="Version"><Value mono>{settings.version}</Value></Row>
          </>
        )
      default:
        return null
    }
  }

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.12 }}
    >
      <div className="absolute inset-0 backdrop-blur-sm" style={{ background: 'var(--color-backdrop)' }} onClick={onClose} />

      <motion.div
        ref={windowRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        tabIndex={-1}
        className="glass-modal relative flex w-[760px] max-w-full h-[min(540px,calc(100vh-2rem))] overflow-hidden rounded-2xl focus:outline-none"
        style={{ boxShadow: '0 24px 60px rgba(0,0,0,0.45)' }}
        initial={{ scale: 0.96, opacity: 0, y: 8 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        // A short exit: the window is gone (and the editor answers keys again) at once
        exit={{ scale: 0.96, opacity: 0, y: 8, transition: { duration: 0.12 } }}
        transition={{ type: 'spring', stiffness: 420, damping: 32 }}
        onKeyDown={onKeyDown}
      >
        <nav className="w-[190px] shrink-0 border-r px-2.5 py-3.5" style={line}>
          <h2 id="settings-title" className="mx-2 mt-0.5 mb-3 text-[13px] font-bold text-white">Settings</h2>
          <div role="tablist" aria-orientation="vertical" aria-labelledby="settings-title" onKeyDown={onTabKey}>
            {GROUPS.map((g) => {
              const open = g.id === group
              return (
                <button
                  key={g.id}
                  id={`settings-tab-${g.id}`}
                  type="button"
                  role="tab"
                  aria-selected={open}
                  aria-controls="settings-group"
                  tabIndex={open ? 0 : -1}
                  onClick={() => onGroup(g.id)}
                  className={cn(
                    'mb-0.5 flex w-full items-center justify-between border-l-2 py-[7px] pl-3 pr-2.5 text-left text-[12px] transition-colors',
                    'focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-brand-500/60',
                    open ? 'border-brand-500 bg-white/5 text-white' : 'border-transparent text-ink-300 hover:bg-white/5 hover:text-white'
                  )}
                >
                  {g.label}
                  {unsaved(g.id) && <span className="h-1.5 w-1.5 rounded-full bg-brand-400" title="Not saved yet" />}
                </button>
              )
            })}
          </div>
        </nav>

        <section
          id="settings-group"
          role="tabpanel"
          aria-labelledby={`settings-tab-${group}`}
          className="flex-1 min-w-0 overflow-y-auto px-[22px] py-[18px]"
        >
          <h3 className="mb-3.5 pr-8 text-[15px] font-semibold text-white">
            {GROUPS.find((g) => g.id === group)?.label}
          </h3>
          {group === 'appearance' ? (
            <AppearanceGroup />
          ) : data ? (
            groupBody(data)
          ) : isError ? (
            <Note tone="warn">
              {(error as Error)?.message || "Couldn't load the settings."}{' '}
              <button type="button" onClick={() => refetch()} className="underline hover:text-amber-200">Try again</button>
            </Note>
          ) : (
            <p className="text-[11px] text-ink-500">Loading…</p>
          )}
        </section>

        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-3 top-2.5 flex h-6 w-6 items-center justify-center rounded-md text-ink-400 transition-colors hover:bg-white/10 hover:text-white"
        >
          <X size={14} />
        </button>
      </motion.div>
    </motion.div>
  )
}

export function SettingsDialog() {
  const open = useEditorStore((s) => s.settingsOpen)
  const setOpen = useEditorStore((s) => s.setSettingsOpen)
  const close = useCallback(() => setOpen(false), [setOpen])
  // Opens on the group it was left on
  const [group, setGroup] = useState<SettingsGroup>('keys')

  // ⌘, opens Settings, as in other Mac apps
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ',') {
        e.preventDefault()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setOpen])

  return (
    <AnimatePresence>
      {open && <SettingsWindow group={group} onGroup={setGroup} onClose={close} />}
    </AnimatePresence>
  )
}
