// src/App.tsx
// Single-window app: the editor IS the application. Projects and Voices live
// in the editor's icon-rail dock and Settings in a window over it — the old
// standalone pages and grey nav sidebar are gone.
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { BackendGate } from '@/components/layout/BackendGate'
import EditorPage from '@/pages/EditorPage'
import { SettingsDialog } from '@/components/settings/SettingsDialog'
import { useEditorStore } from '@/store/editorStore'
import { initTheme } from '@/store/themeStore'

// Restore persisted theme before first render
initTheme()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5000,
      refetchOnWindowFocus: false,
    },
  },
})

const toasterStyle = {
  background: 'var(--toast-bg)',
  border: '1px solid var(--toast-border)',
  color: 'var(--toast-color)',
  fontFamily: 'var(--font-sans)',
}

// Routes stay keyed by pathname so every navigation remounts the editor —
// the VideoPlayer/Timeline lifecycles and the per-session store reset rely
// on that remount, so don't remove the key.
function AppRoutes() {
  const location = useLocation()
  return (
    <Routes location={location} key={location.pathname}>
      <Route path="/"                                  element={<EditorPage />} />
      <Route path="/projects/:projectId"               element={<EditorPage />} />
      <Route path="/projects/:projectId/jobs/:jobId"   element={<EditorPage />} />
      <Route path="*"                                  element={<Navigate to="/" replace />} />
    </Routes>
  )
}

// Settings is a window over every route, outside the keyed routes so a
// navigation can't reset it. While it's open everything behind it is inert:
// nothing there can take focus, clicks or keys.
function AppScreens() {
  const settingsOpen = useEditorStore((s) => s.settingsOpen)
  return (
    <>
      <div className="contents" inert={settingsOpen}>
        <AppRoutes />
      </div>
      <SettingsDialog />
    </>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrowserRouter>
          {/* Nothing renders (and no query fires) until the API really answers */}
          <BackendGate>
            <AppScreens />
          </BackendGate>
          <Toaster position="bottom-right" toastOptions={{ style: toasterStyle }} />
        </BrowserRouter>
      </ErrorBoundary>
    </QueryClientProvider>
  )
}
