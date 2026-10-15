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

  // One step at a time: a second press while the first is still talking to the
  // server would apply the same entry twice and pop one that was never undone.
  // A step that fails is dropped (and the error rethrown for the caller to
  // show) — left on top, it would fail again on every press and block the rest.
  undo: async () => {
    const { past, isApplying } = get()
    if (isApplying || past.length === 0) return
    const entry = past[past.length - 1]
    set({ isApplying: true })
    try {
      await entry.undo()
      set((s) => ({
        past: s.past.slice(0, -1),
        future: [...s.future, entry],
      }))
    } catch (err) {
      set((s) => ({ past: s.past.filter((e) => e !== entry) }))
      throw err
    } finally {
      set({ isApplying: false })
    }
  },

  redo: async () => {
    const { future, isApplying } = get()
    if (isApplying || future.length === 0) return
    const entry = future[future.length - 1]
    set({ isApplying: true })
    try {
      await entry.redo()
      set((s) => ({
        future: s.future.slice(0, -1),
        past: [...s.past, entry],
      }))
    } catch (err) {
      set((s) => ({ future: s.future.filter((e) => e !== entry) }))
      throw err
    } finally {
      set({ isApplying: false })
    }
  },

  clear: () => set({ past: [], future: [] }),
}))
