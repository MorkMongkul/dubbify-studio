// src/lib/desktop.ts
// Helpers for the Tauri desktop shell. Every function degrades gracefully in a
// plain browser (dev via `npm run dev`), so components can call them
// unconditionally.

/** True when running inside the Tauri webview rather than a normal browser. */
export function isDesktop(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

/**
 * Outcome of asking the user where to save the exported video.
 *
 * These used to be collapsed into `string | null | undefined`, where
 * `undefined` meant BOTH "we're in a browser" and "the native dialog blew up".
 * The caller couldn't tell those apart, so a broken dialog silently became a
 * no-destination export that ended in a `window.open()` the desktop webview
 * ignores — the export simply appeared to do nothing.
 */
export type SaveTarget =
  /** User picked a destination. */
  | { kind: 'path'; path: string }
  /** User dismissed the dialog — abort, this is not an error. */
  | { kind: 'cancelled' }
  /** No native dialog here (browser) or it failed; `reason` explains which. */
  | { kind: 'unavailable'; reason: string }

/**
 * Native "Save As" dialog for the exported video.
 *
 * Never throws: a failure is reported as `unavailable` with the underlying
 * message so the caller can say why it's falling back instead of going quiet.
 */
export async function chooseVideoSavePath(defaultName: string): Promise<SaveTarget> {
  if (!isDesktop()) {
    return { kind: 'unavailable', reason: 'Running in a browser, not the desktop app' }
  }
  try {
    const { save } = await import('@tauri-apps/plugin-dialog')
    const picked = await save({
      defaultPath: defaultName,
      filters: [{ name: 'Video', extensions: ['mp4'] }],
    })
    return picked ? { kind: 'path', path: picked } : { kind: 'cancelled' }
  } catch (err) {
    console.warn('Native save dialog failed', err)
    return {
      kind: 'unavailable',
      reason: err instanceof Error ? err.message : String(err),
    }
  }
}

/** Why the bundled backend isn't available — sent by the desktop shell. */
export interface BackendProblem {
  message: string
  /** Folder holding sidecar.log and backend.log. */
  log_dir: string
}

/**
 * Subscribe to the desktop shell's `backend-failed` (never started) and
 * `backend-stopped` (crashed mid-session) events. A no-op in the browser.
 * Returns an unsubscribe function.
 */
export function onBackendProblem(handler: (problem: BackendProblem) => void): () => void {
  if (!isDesktop()) return () => {}
  let disposed = false
  const unlisteners: Array<() => void> = []
  void import('@tauri-apps/api/event').then(({ listen }) => {
    for (const name of ['backend-failed', 'backend-stopped']) {
      listen<BackendProblem>(name, (event) => handler(event.payload))
        .then((unlisten) => (disposed ? unlisten() : unlisteners.push(unlisten)))
        .catch((err) => console.warn(`Could not listen for ${name}`, err))
    }
  })
  return () => {
    disposed = true
    unlisteners.forEach((unlisten) => unlisten())
  }
}

/** True on the desktop app's built-in start-up page, before it moves to the backend. */
export function isDesktopStartupPage(): boolean {
  return isDesktop() && window.location.protocol === 'tauri:'
}
