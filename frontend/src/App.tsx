// src/App.tsx
// Single-window app: the editor IS the application. Projects, Voices and
// Settings live in the editor's icon-rail dock — the old standalone pages
// and grey nav sidebar are gone.
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { BackendGate } from '@/components/layout/BackendGate'
import EditorPage from '@/pages/EditorPage'
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

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrowserRouter>
          {/* Nothing renders (and no query fires) until the API really answers */}
          <BackendGate>
            <AppRoutes />
          </BackendGate>
          <Toaster position="bottom-right" toastOptions={{ style: toasterStyle }} />
        </BrowserRouter>
      </ErrorBoundary>
    </QueryClientProvider>
  )
}
