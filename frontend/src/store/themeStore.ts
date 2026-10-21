// src/store/themeStore.ts
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type Theme = 'dark' | 'light'

const THEME_KEY = 'movelo-theme'

// Saved as 'dubify-theme' before the Movelo rename.
try {
  const previous = localStorage.getItem('dubify-theme')
  if (previous !== null && localStorage.getItem(THEME_KEY) === null) localStorage.setItem(THEME_KEY, previous)
  localStorage.removeItem('dubify-theme')
} catch {
  // storage unavailable — the default theme applies
}

interface ThemeStore {
  theme: Theme
  setTheme: (t: Theme) => void
}

export const useThemeStore = create<ThemeStore>()(
  persist(
    (set) => ({
      theme: 'dark',
      setTheme: (theme) => {
        set({ theme })
        applyTheme(theme)
      },
    }),
    { name: THEME_KEY }
  )
)

export function applyTheme(theme: Theme) {
  const root = document.documentElement
  if (theme === 'light') {
    root.setAttribute('data-theme', 'light')
  } else {
    root.removeAttribute('data-theme')
  }
}

// Call once on boot to restore persisted theme
export function initTheme() {
  const stored = localStorage.getItem(THEME_KEY)
  try {
    const parsed = JSON.parse(stored ?? '{}')
    const theme: Theme = parsed?.state?.theme ?? 'dark'
    applyTheme(theme)
  } catch {
    applyTheme('dark')
  }
}
