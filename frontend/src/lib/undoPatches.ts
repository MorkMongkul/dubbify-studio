// What an undo sends back: every setting the edit may have changed — the
// server clamps fades to a clip's length, so a trim or a split can shorten
// them and its undo must give them back. Pure logic: `npm test` runs it.
import type { LayoutChange } from './tracks.ts'
import type { Segment, SegmentUpdate } from '../types/index.ts'

/** A clip's place on the timeline, as a layout change (for undo / redo). */
export function layoutPlace(s: Segment): LayoutChange {
  return {
    id: s.id, start_time: s.start_time, end_time: s.end_time, lane_index: s.lane_index ?? 0,
    voice_offset: s.voice_offset ?? 0, fade_in_secs: s.fade_in_secs ?? 0, fade_out_secs: s.fade_out_secs ?? 0,
  }
}

/** Undo of a split: the first half gets back what the whole clip had. */
export function splitUndoPatch(before: Segment): SegmentUpdate {
  return {
    end_time: before.end_time,
    lane_index: before.lane_index ?? 0,
    khmer_text: before.khmer_text,
    source_text: before.source_text,
    english_text: before.english_text,
    source_start: before.source_start ?? null,
    source_end: before.source_end ?? null,
    voice_text: before.voice_text ?? null,
    fade_in_secs: before.fade_in_secs ?? 0,
    fade_out_secs: before.fade_out_secs ?? 0,
  }
}
