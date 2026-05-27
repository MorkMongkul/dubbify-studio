// src/App.tsx
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AnimatePresence } from 'framer-motion'
import { AppShell } from '@/components/layout/AppShell'
import ProjectsPage from '@/pages/ProjectsPage'
import WorkspacePage from '@/pages/WorkspacePage'
import SettingsPage from '@/pages/SettingsPage'
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

function AnimatedRoutes() {
  const location = useLocation()
  return (
    <AnimatePresence mode="wait" initial={false}>
      <Routes location={location} key={location.pathname}>
        <Route path="/"                                  element={<Navigate to="/projects" replace />} />
        <Route path="/projects"                          element={<ProjectsPage />} />
        <Route path="/projects/:projectId"               element={<WorkspacePage />} />
        <Route path="/projects/:projectId/jobs/:jobId"   element={<WorkspacePage />} />
        <Route path="/settings"                          element={<SettingsPage />} />
        <Route path="*"                                  element={<Navigate to="/projects" replace />} />
      </Routes>
    </AnimatePresence>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppShell>
          <AnimatedRoutes />
        </AppShell>
      </BrowserRouter>
    </QueryClientProvider>
  )
}