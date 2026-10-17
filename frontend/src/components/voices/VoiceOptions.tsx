import type { Voice } from '@/types'

/** <option>s for a voice <select>: the user's own voices, then the built-in library. */
export function VoiceOptions({ voices }: { voices: Voice[] }) {
  const mine = voices.filter((v) => !v.builtin)
  const builtIn = voices.filter((v) => v.builtin).sort((a, b) => a.id.localeCompare(b.id))
  return (
    <>
      {mine.length > 0 && (
        <optgroup label="My voices">
          {mine.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </optgroup>
      )}
      {builtIn.length > 0 && (
        <optgroup label="Built-in voices">
          {builtIn.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
        </optgroup>
      )}
    </>
  )
}
