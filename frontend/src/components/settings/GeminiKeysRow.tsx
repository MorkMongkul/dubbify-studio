// Settings → Keys: the Gemini keys. Any number (up to 50) go in one box, a key per line; a .txt in the
// `api_key01=AIza…` form can be imported. Movelo spreads requests over them and stays under each key's limits. Keys
// are write-only: the server never sends them back, so the box starts empty and only what is typed is saved.
import { useRef, useState } from 'react'
import { Activity, Eye, EyeOff, FileUp, X } from 'lucide-react'
import { toast } from 'sonner'
import { importedKeys, keysBadge, MAX_GEMINI_KEYS, parseKeys } from '@/lib/geminiKeys'
import { isDesktop } from '@/lib/desktop'
import { cn } from '@/lib/utils'

interface Props {
  count: number                      // keys saved
  value: string                      // what has been typed or imported, not yet saved
  disabled: boolean
  onChange: (v: string) => void
  onRemove: () => void
}

export function GeminiKeysRow({ count, value, disabled, onChange, onRemove }: Props) {
  const [shown, setShown] = useState(false)
  const [monitor, setMonitor] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const typed = parseKeys(value).length

  const importFile = async (file: File | undefined) => {
    if (!file) return
    if (file.size > 100_000) return void toast.error('That file is too big to be a list of keys')
    const { text, count: n } = importedKeys(await file.text())
    if (!n) return void toast.error('No keys found in that file')
    if (n > MAX_GEMINI_KEYS) return void toast.error(`At most ${MAX_GEMINI_KEYS} keys`)
    onChange(text)
    toast.success(`${n} ${n === 1 ? 'key' : 'keys'} ready — press Save to use ${n === 1 ? 'it' : 'them'}`)
  }

  // The desktop window ignores window.open, so the monitor opens in a window of its own; a browser gets a tab.
  const openMonitor = () => (isDesktop() ? setMonitor(true) : void window.open(`${window.location.origin}/gateway`, '_blank'))

  return (
    <div className="py-2.5 border-b" style={{ borderColor: 'var(--color-border)' }}>
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className="text-[11.5px] font-medium text-ink-200">Gemini API keys</span>
        <span className="flex items-center gap-2">
          <span className={cn('text-[10.5px] px-1.5 py-px rounded-full border',
            count > 0 ? 'text-emerald-400 border-emerald-500/30' : 'text-ink-500 border-ink-700/60')}>
            {keysBadge(count)}
          </span>
          {count > 0 && !disabled && (
            <button type="button" onClick={onRemove} className="text-[10.5px] text-ink-500 hover:text-red-400 transition-colors">
              Remove all
            </button>
          )}
        </span>
      </div>
      <textarea
        aria-label="Gemini API keys" rows={4} spellCheck={false} autoComplete="off" disabled={disabled}
        placeholder={count > 0 ? '•••••••• Saved. Paste every key you want to use to replace them.'
          : 'Paste one or more Gemini API keys (one per line)...'}
        value={value} onChange={(e) => onChange(e.target.value)}
        style={shown ? undefined : ({ WebkitTextSecurity: 'disc' } as React.CSSProperties)}
        className="w-full rounded-[7px] border border-ink-700/60 bg-ink-950 px-2.5 py-2 font-mono text-[11px] text-ink-100 leading-relaxed
          placeholder:text-ink-600 focus:outline-none focus:border-brand-500/60 focus:ring-1 focus:ring-brand-500/40 disabled:opacity-60 resize-none"
      />
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[10.5px] text-ink-500">
        <button type="button" onClick={() => setShown((s) => !s)} className="inline-flex items-center gap-1 hover:text-ink-200 transition-colors">
          {shown ? <EyeOff size={11} /> : <Eye size={11} />}{shown ? 'Hide' : 'Show'}
        </button>
        <button type="button" disabled={disabled} onClick={() => fileRef.current?.click()}
          className="inline-flex items-center gap-1 hover:text-ink-200 transition-colors disabled:opacity-50">
          <FileUp size={11} />Import from .txt
        </button>
        <input ref={fileRef} type="file" accept=".txt,text/plain" className="hidden" aria-label="Import keys from a .txt file"
          onChange={(e) => { void importFile(e.target.files?.[0]); e.target.value = '' }} />
        <button type="button" onClick={openMonitor} className="inline-flex items-center gap-1 hover:text-ink-200 transition-colors">
          <Activity size={11} />Open Key Monitor
        </button>
        {typed > 0 && <span>{typed} {typed === 1 ? 'key' : 'keys'} typed, not saved yet</span>}
      </div>
      <p className="mt-1.5 text-[10.5px] leading-snug text-ink-600">
        Your own keys, kept on this Mac — none come with the app. One key per line, or a .txt with lines like
        <span className="font-mono"> api_key01=AIza…</span>. Saving replaces every saved key, so include all you want to use.
      </p>
      {monitor && (
        <div role="dialog" aria-label="Key Monitor" className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-6">
          <div className="relative flex h-[85vh] w-[min(1100px,100%)] flex-col overflow-hidden rounded-xl border border-ink-700/60 bg-ink-950">
            <button type="button" aria-label="Close the Key Monitor" onClick={() => setMonitor(false)}
              className="absolute right-3 top-3 z-10 rounded-md p-1 text-ink-400 hover:text-ink-100"><X size={16} /></button>
            <iframe title="Key Monitor" src="/gateway" className="h-full w-full border-0" />
          </div>
        </div>
      )}
    </div>
  )
}
