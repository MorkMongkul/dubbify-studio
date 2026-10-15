// src/components/layout/BackendGate.tsx
// Holds the editor back until the API actually answers.
//
// The desktop app starts its bundled backend at launch and shows this page
// (tauri://localhost) meanwhile; the shell moves the window to the backend
// once THIS launch's backend answers. If it never starts, or dies mid-session,
// the shell sends an event and this screen says so — with the log folder —
// instead of spinning forever. In the browser it simply waits for /health.
import { useEffect, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Zap, AlertCircle, RefreshCw, Copy } from 'lucide-react'
import { health } from '@/api/client'
import { isDesktopStartupPage, onBackendProblem, type BackendProblem } from '@/lib/desktop'

/** Long enough that a warm backend never flashes the splash. */
const SPLASH_DELAY_MS = 400
/** After this we stop saying "starting" and offer a retry instead. */
const SLOW_START_MS = 45_000

export function BackendGate({ children }: { children: ReactNode }) {
  // On the desktop start-up page the API isn't reachable (relative URLs hit
  // the bundled assets) — the shell navigates away once the backend is up.
  const startupPage = isDesktopStartupPage()
  const { data, isSuccess, refetch, isFetching } = useQuery({
    queryKey: ['backend-ready'],
    queryFn: health.check,
    enabled: !startupPage,
    // Poll briskly while we're waiting; once up, this query goes quiet.
    refetchInterval: (q) => (isReady(q.state.data) ? false : 1000),
    retry: false,
    staleTime: 0,
  })

  const ready = isSuccess && isReady(data)
  const [problem, setProblem] = useState<BackendProblem | null>(null)
  useEffect(() => onBackendProblem(setProblem), [])

  const [showSplash, setShowSplash] = useState(false)
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const a = setTimeout(() => setShowSplash(true), SPLASH_DELAY_MS)
    const b = setTimeout(() => setSlow(true), SLOW_START_MS)
    return () => { clearTimeout(a); clearTimeout(b) }
  }, [])

  if (problem) return <BackendProblemScreen problem={problem} />
  if (ready) return <>{children}</>
  if (!showSplash) return null

  return (
    <div className="h-screen w-screen flex flex-col items-center justify-center gap-4 bg-surface-0 text-white select-none">
      <div className="h-12 w-12 rounded-2xl bg-brand flex items-center justify-center shadow-glow">
        <Zap size={22} className="text-white" fill="white" />
      </div>
      <div className="text-center space-y-1">
        <p className="text-sm font-bold">
          Dubify<span className="text-brand-300">Studio</span>
        </p>
        {slow ? (
          <>
            <p className="text-[12px] text-amber-400/90 flex items-center justify-center gap-1.5">
              <AlertCircle size={12} /> The backend is taking longer than usual
            </p>
            <p className="text-[11px] text-white/40 max-w-[300px] leading-relaxed">
              Your projects are safe — this is only the app's engine starting
              up. The first launch after an update can take a minute.
            </p>
          </>
        ) : (
          <p className="text-[12px] text-white/45">Starting backend…</p>
        )}
      </div>

      {slow && !startupPage ? (
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 px-3 h-8 rounded-lg bg-white/[0.06] hover:bg-white/10 text-[12px] font-medium text-white/80 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
          {isFetching ? 'Checking…' : 'Try again'}
        </button>
      ) : (
        <div className="h-1 w-40 rounded-full bg-white/8 overflow-hidden">
          <div className="h-full w-1/3 rounded-full bg-brand animate-[shimmer_1.2s_ease-in-out_infinite]" />
        </div>
      )}
    </div>
  )
}

function BackendProblemScreen({ problem }: { problem: BackendProblem }) {
  const [copied, setCopied] = useState(false)
  const copyLogFolder = () => {
    navigator.clipboard?.writeText(problem.log_dir).then(() => setCopied(true), () => setCopied(false))
  }
  return (
    <div className="h-screen w-screen flex flex-col items-center justify-center gap-4 bg-surface-0 text-white select-none p-6">
      <div className="h-12 w-12 rounded-2xl bg-red-500/15 flex items-center justify-center">
        <AlertCircle size={22} className="text-red-400" />
      </div>
      <div className="text-center space-y-1.5 max-w-[380px]">
        <p className="text-sm font-bold">Dubify Studio can't continue</p>
        <p className="text-[12px] text-white/70">{problem.message}</p>
        <p className="text-[11px] text-white/40 leading-relaxed">
          Quit and reopen the app. Your projects are stored on this Mac and are safe.
          If it keeps happening, send the files in this folder to support:
        </p>
        <p className="text-[10.5px] font-mono text-white/55 break-all select-text">{problem.log_dir}</p>
      </div>
      <button
        onClick={copyLogFolder}
        className="flex items-center gap-1.5 px-3 h-8 rounded-lg bg-white/[0.06] hover:bg-white/10 text-[12px] font-medium text-white/80 transition-colors"
      >
        <Copy size={12} />
        {copied ? 'Copied' : 'Copy log folder path'}
      </button>
    </div>
  )
}

/** A real health payload — not an HTML page the asset protocol handed back. */
function isReady(d: unknown): boolean {
  return !!d && typeof d === 'object' && (d as { status?: string }).status === 'ok'
}
