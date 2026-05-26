// src/store/themeStore.ts
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

type Theme = 'dark' | 'light'

interface ThemeStore {
  theme: Theme
  setTheme: (t: Theme) => void
  toggleTheme: () => void
}

export const useThemeStore = create<ThemeStore>()(
  persist(
    (set, get) => ({
      theme: 'dark',
      setTheme: (theme) => {
        set({ theme })
        applyTheme(theme)
      },
      toggleTheme: () => {
        const next = get().theme === 'dark' ? 'light' : 'dark'
        set({ theme: next })
        applyTheme(next)
      },
    }),
    { name: 'dubify-theme' }
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
  const stored = localStorage.getItem('dubify-theme')
  try {
    const parsed = JSON.parse(stored ?? '{}')
    const theme: Theme = parsed?.state?.theme ?? 'dark'
    applyTheme(theme)
  } catch {
    applyTheme('dark')
  }
}
