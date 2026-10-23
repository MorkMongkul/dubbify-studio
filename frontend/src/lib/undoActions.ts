// Undo / redo with a word about what happened — shared by ⌘Z / ⇧⌘Z and the
// timeline's toolbar buttons. A step that fails is dropped from the history
// (historyStore) and said so.
import { toast } from 'sonner'
import { useHistoryStore } from '@/store/historyStore'

const failed = (verb: string, label: string) => (err: unknown) => {
  toast.error(`Couldn't ${verb} "${label}" — ${(err as Error)?.message || 'request failed'}. It was removed from history.`)
}

export async function undoWithToast(): Promise<void> {
  const { past, undo } = useHistoryStore.getState()
  const label = past[past.length - 1]?.label
  if (!label) return
  await undo().then(() => { toast.message(`Undid: ${label}`) }, failed('undo', label))
}

export async function redoWithToast(): Promise<void> {
  const { future, redo } = useHistoryStore.getState()
  const label = future[future.length - 1]?.label
  if (!label) return
  await redo().then(() => { toast.message(`Redid: ${label}`) }, failed('redo', label))
}
