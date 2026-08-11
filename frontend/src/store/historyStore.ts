// src/store/historyStore.ts
// App-wide undo/redo stack. Every entry is a pair of async actions that
// re-apply the previous/next server state for one edit — since all editor
// state is server-persisted (not just client state), "undo" means firing
// the inverse API call, not popping a local snapshot.

import { create } from 'zustand'

export interface HistoryEntry {
  label: string
  undo: () => Promise<void>
  redo: () => Promise<void>
}

const MAX_HISTORY = 50

interface HistoryState {
  past: HistoryEntry[]
  future: HistoryEntry[]
  isApplying: boolean
  push: (entry: HistoryEntry) => void
  undo: () => Promise<void>
  redo: () => Promise<void>
  clear: () => void
}

export const useHistoryStore = create<HistoryState>((set, get) => ({
  past: [],
  future: [],
  isApplying: false,

  push: (entry) => {
    // Undo/redo re-fire the same kind of API call the original edit did —
    // without this guard, applying `undo` would record itself as a brand
    // new action and corrupt the stack.
    if (get().isApplying) return
    set((s) => ({
      past: [...s.past, entry].slice(-MAX_HISTORY),
      future: [],
    }))
  },

  undo: async () => {
    const { past } = get()
    if (past.length === 0) return
    const entry = past[past.length - 1]
    set({ isApplying: true })
    try {
      await entry.undo()
      set((s) => ({
        past: s.past.slice(0, -1),
        future: [...s.future, entry],
      }))
    } finally {
      set({ isApplying: false })
    }
  },

  redo: async () => {
    const { future } = get()
    if (future.length === 0) return
    const entry = future[future.length - 1]
    set({ isApplying: true })
    try {
      await entry.redo()
      set((s) => ({
        future: s.future.slice(0, -1),
        past: [...s.past, entry],
      }))
    } finally {
      set({ isApplying: false })
    }
  },

  clear: () => set({ past: [], future: [] }),
}))
