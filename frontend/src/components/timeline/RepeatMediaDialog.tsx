// src/components/timeline/RepeatMediaDialog.tsx
// "Repeat…" on a media clip (0.5.8): a lower third or sponsor card that comes back — shown N seconds, again every
// M seconds from where the clip starts, until the end of the episode or a time you choose. Movelo places the copies
// (lib/media repeatClip) in one undo step; each can still be moved or deleted afterwards.
import { useState } from 'react'
import type { MediaClip } from '@/types'
import type { MediaEditing } from '@/hooks/useMediaEditing'
import { repeatStarts } from '@/lib/media'
import { Modal, InputField } from '@/components/ui/Modal'
import { Button } from '@/components/ui/Button'

const clock = (secs: number) => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`
const asNumber = (text: string) => (text.trim() === '' ? NaN : Number(text))

export function RepeatMediaDialog({
  clip, edit, duration, onClose,
}: { clip: MediaClip; edit: MediaEditing; duration: number; onClose: () => void }) {
  const [show, setShow] = useState(String(Math.round((clip.end_time - clip.start_time) * 10) / 10))
  const [every, setEvery] = useState('60')
  const [toEnd, setToEnd] = useState(true)
  const [until, setUntil] = useState(String(Math.floor(duration)))

  const showSecs = asNumber(show)
  const everySecs = asNumber(every)
  const untilSecs = toEnd ? duration : asNumber(until)
  const valid = showSecs > 0 && everySecs > 0 && untilSecs > clip.start_time
  const starts = valid ? repeatStarts(clip.start_time, { showSecs, everySecs, untilSecs }, duration) : []
  const gapNote = valid && everySecs < showSecs ? ' (the gap can\'t be shorter than the show, so it is the same)' : ''

  const place = () => {
    if (!valid) return
    if (edit.repeat(clip, { showSecs, everySecs, untilSecs })) onClose()
  }

  return (
    <Modal open onClose={onClose} title="Repeat" size="sm"
      description="Show this clip again and again. You can still move or delete any one of them afterwards.">
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); place() }}>
        <div className="grid grid-cols-2 gap-3">
          <InputField label="Show for (seconds)" type="number" min={0.1} step="any" value={show} onChange={(e) => setShow(e.target.value)} autoFocus />
          <InputField label="Again every (seconds)" type="number" min={1} step="any" value={every} onChange={(e) => setEvery(e.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-primary)' }}>
          <input type="checkbox" checked={toEnd} onChange={(e) => setToEnd(e.target.checked)} />
          Until the end of the episode ({clock(duration)})
        </label>
        {!toEnd && (
          <InputField label="Until (seconds from the start)" type="number" min={0} step="any" value={until} onChange={(e) => setUntil(e.target.value)} />
        )}
        <p className="text-xs leading-snug" style={{ color: 'var(--color-text-secondary)' }}>
          {!valid ? 'Fill in the three numbers.'
            : starts.length < 2 ? "Nothing to repeat: the show and the gap don't fit before the end."
            : `${starts.length} appearances, from ${clock(starts[0])} to ${clock(starts[starts.length - 1])}${gapNote}.`}
        </p>
        <div className="pt-1 flex gap-3">
          <Button type="button" variant="ghost" className="flex-1" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="default" className="flex-1" disabled={starts.length < 2}>Repeat</Button>
        </div>
      </form>
    </Modal>
  )
}
