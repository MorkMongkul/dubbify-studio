// src/components/ErrorBoundary.tsx
// Catches render errors so the app shows a recoverable screen instead of a
// blank white page (e.g. on refresh mid-pipeline).
import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: unknown) {
    // Surface the real error in the console for debugging
    console.error('App render error:', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen w-screen items-center justify-center bg-zinc-950 p-6">
          <div className="max-w-md w-full rounded-xl border border-zinc-800 bg-zinc-900 p-6 text-center">
            <div className="mx-auto mb-3 h-11 w-11 rounded-full bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400 text-xl">
              !
            </div>
            <h1 className="text-sm font-bold text-white mb-1">Something went wrong</h1>
            <p className="text-[11px] text-zinc-400 mb-4">
              The view hit an error while rendering. Your work is saved on the server — reloading usually fixes it.
            </p>
            <pre className="text-[10px] text-left text-red-300/80 bg-black/40 rounded-md p-2 mb-4 max-h-32 overflow-auto whitespace-pre-wrap">
              {this.state.error.message}
            </pre>
            <div className="flex gap-2 justify-center">
              <button
                onClick={() => this.setState({ error: null })}
                className="px-3 py-1.5 rounded-md bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-medium"
              >
                Try again
              </button>
              <button
                onClick={() => window.location.reload()}
                className="px-3 py-1.5 rounded-md bg-brand-400 hover:bg-brand-400/90 text-white text-xs font-medium"
              >
                Reload
              </button>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
