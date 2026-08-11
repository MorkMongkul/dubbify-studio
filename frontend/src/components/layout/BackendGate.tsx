// src/components/layout/BackendGate.tsx
// Holds the editor back until the API actually answers.
//
// The packaged app spawns its FastAPI sidecar at launch, and a cold Neon
// database can take ~25s to accept connections. Until then the webview is
// still on tauri://localhost, where every /api/v1 path resolves to the SPA's
// own index.html — so requests "succeeded" with an HTML body and the editor
// rendered an empty, apparently-wiped workspace. Mounting the app only once
// /health returns a real payload removes that whole window.
import { useEffect, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Zap, AlertCircle, RefreshCw } from 'lucide-react'
import { health } from '@/api/client'

/** Long enough that a warm backend never flashes the splash. */
const SPLASH_DELAY_MS = 400
/** After this we stop saying "starting" and offer a retry instead. */
const SLOW_START_MS = 45_000

export function BackendGate({ children }: { children: ReactNode }) {
  const { data, isSuccess, refetch, isFetching } = useQuery({
    queryKey: ['backend-ready'],
    queryFn: health.check,
    // Poll briskly while we're waiting; once up, this query goes quiet.
    refetchInterval: (q) => (isReady(q.state.data) ? false : 1000),
    retry: false,
    staleTime: 0,
  })

  const ready = isSuccess && isReady(data)

  const [showSplash, setShowSplash] = useState(false)
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const a = setTimeout(() => setShowSplash(true), SPLASH_DELAY_MS)
    const b = setTimeout(() => setSlow(true), SLOW_START_MS)
    return () => { clearTimeout(a); clearTimeout(b) }
  }, [])

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
              Your projects are safe on the server. This is only the local API
              starting up — nothing has been lost.
            </p>
          </>
        ) : (
          <p className="text-[12px] text-white/45">Starting backend…</p>
        )}
      </div>

      {slow ? (
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

/** A real health payload — not an HTML page the asset protocol handed back. */
function isReady(d: unknown): boolean {
  return !!d && typeof d === 'object' && (d as { status?: string }).status === 'ok'
}
