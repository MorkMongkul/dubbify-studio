// The editor shows the server's segments with the edits still in flight laid
// over them (editorStore.segmentPositions): a drop or trim waiting for the
// server, text typed but not saved yet. The voice — every tts_* field and the
// voice's own settings — always comes from the server; only where the clip is
// (start, end, track, the trimmed-off start) and unsaved text can be overlaid.
// Pure logic, no runtime imports: `npm test` runs it.
import type { Segment } from '../types/index.ts'

export type SegmentOverlay = Partial<Pick<Segment,
  'start_time' | 'end_time' | 'lane_index' | 'voice_offset' | 'khmer_text' | 'fade_in_secs' | 'fade_out_secs'>>

const fromServerOnly = (key: string) => key.startsWith('tts_') || (key.startsWith('voice_') && key !== 'voice_offset') || key === 'fit_rate'

export function mergeOverlay(server: Segment[], overlay: Record<string, Partial<Segment>>): Segment[] {
  return server.map((s) => {
    const over = overlay[s.id]
    if (!over) return s
    const merged = { ...s }
    for (const [key, value] of Object.entries(over)) {
      if (value === undefined || fromServerOnly(key)) continue
      ;(merged as Record<string, unknown>)[key] = value
    }
    return merged
  })
}
