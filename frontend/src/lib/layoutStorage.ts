// src/lib/layoutStorage.ts
// The editor's layout (lib/layouts) is kept on this Mac, like CapCut's. Browser
// storage can be missing or refuse (a private window, cleared site data): then
// the editor opens with the default and keeps a choice for this session only.
import { readLayout, type LayoutName } from './layouts'

const KEY = 'movelo.layout'

export function loadLayout(): LayoutName {
  try { return readLayout(localStorage.getItem(KEY)) } catch { return 'default' }
}

export function saveLayout(name: LayoutName): void {
  try { localStorage.setItem(KEY, name) } catch { /* kept for this session only */ }
}
