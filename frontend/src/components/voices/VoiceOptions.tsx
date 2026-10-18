import type { Voice } from '@/types'
import { groupVoices, pickerLabel } from '@/lib/voiceTags'

/** <option>s for a voice <select>, grouped like the Voices tab: My voices, then each section. */
export function VoiceOptions({ voices }: { voices: Voice[] }) {
  return (
    <>
      {groupVoices(voices).map((group) => (
        <optgroup key={group.key} label={group.label}>
          {group.voices.map((v) => <option key={v.id} value={v.id}>{pickerLabel(v)}</option>)}
        </optgroup>
      ))}
    </>
  )
}
