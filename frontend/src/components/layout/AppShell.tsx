// src/components/layout/AppShell.tsx
import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { AnimatePresence } from 'framer-motion'
import { AppSidebar } from './AppSidebar'
import { Toaster } from 'sonner'

interface AppShellProps {
  children: React.ReactNode
}

// Editor has its own full-screen layout — no sidebar
const EDITOR_PATTERN = /\/projects\/[^/]+\/jobs\//

export function AppShell({ children }: AppShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const location = useLocation()
  const isEditor = EDITOR_PATTERN.test(location.pathname)

  const toasterStyle = {
    background: 'var(--toast-bg)',
    border: '1px solid var(--toast-border)',
    color: 'var(--toast-color)',
    fontFamily: 'var(--font-sans)',
  }

  if (isEditor) {
    return (
      <div className="h-screen w-screen overflow-hidden bg-surface-0 flex flex-col">
        <AnimatePresence mode="wait" initial={false}>
          {children}
        </AnimatePresence>
        <Toaster position="bottom-right" toastOptions={{ style: toasterStyle }} />
      </div>
    )
  }

  return (
    <div className="h-screen w-screen overflow-hidden bg-surface-0 flex">
      <AppSidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((v) => !v)}
      />
      <main
        className="flex-1 flex flex-col min-h-0 overflow-hidden"
        style={{ background: 'var(--color-surface-0)', color: 'var(--color-text-primary)' }}
      >
        <AnimatePresence mode="wait" initial={false}>
          {children}
        </AnimatePresence>
      </main>
      <Toaster position="bottom-right" toastOptions={{ style: toasterStyle }} />
    </div>
  )
}
