// "Who is this?" (0.5.0): every line has a character after analysis; the lines
// Movelo isn't sure about carry who_check, up to two guesses
// [{speaker_id, basis}] — null means sure (or confirmed), [] means to check
// with no guess (always test `!= null`, never `.length`). A pick confirms the
// line (POST /segments/{id}/character) and may rewrite its Khmer for the new
// speaker. Pure logic, no runtime imports: `npm test` runs it.
import type { CharacterPicked, Segment, SegmentUpdate, Speaker, WhoGuess } from '../types/index.ts'

type Line = Pick<Segment, 'id' | 'start_time' | 'who_check' | 'who_confirmed'>
type Character = Pick<Speaker, 'id' | 'label' | 'display_name'>

/** What a guess rests on, as the chip says it. */
export type Basis = 'voice + face' | 'face' | 'voice'

export interface GuessChip<S extends Character = Speaker> {
  speaker: S
  basis: Basis
  /** The key that picks it: 1 is Movelo's best guess. */
  key: 1 | 2
}

/** Still to check: it has guesses (an empty list too) and nobody confirmed it. */
export function isToCheck(seg: Pick<Line, 'who_check' | 'who_confirmed'>): boolean {
  return seg.who_check != null && !seg.who_confirmed
}

// One order for lines starting together, so Next never skips or repeats one
const byTime = (a: Line, b: Line) => a.start_time - b.start_time || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)

/** The lines to check, in time order. */
export function toCheck<S extends Line>(segments: S[]): S[] {
  return segments.filter(isToCheck).sort(byTime)
}

/**
 * The line to check after `afterId` in time, wrapping to the first. With no
 * line being checked (or one that's gone), the first from `from` (the
 * playhead), else the first. Lines in `skip` (a pick on its way) are passed
 * over; the line being checked stays when it's the only one left.
 */
export function nextToCheck<S extends Line>(
  segments: S[],
  afterId: string | null,
  { from = 0, skip }: { from?: number; skip?: ReadonlySet<string> } = {},
): S | null {
  const after = afterId != null ? segments.find((s) => s.id === afterId) : undefined
  const open = toCheck(segments).filter((s) => !skip?.has(s.id))
  const others = open.filter((s) => s.id !== after?.id)
  if (!others.length) return open.find((s) => s.id === after?.id) ?? null
  const ahead = after
    ? others.find((s) => byTime(s, after) > 0)
    : others.find((s) => s.start_time >= from - 1e-3)
  return ahead ?? others[0]
}

/** The chip's words for a guess's basis. */
export function basisOf(raw: string): Basis {
  const b = (raw || '').toLowerCase()
  if (b.includes('face') && b.includes('voice')) return 'voice + face'
  return b.includes('face') ? 'face' : 'voice'
}

/** The line's guesses as chips: keys 1 and 2 in the order shown; a character that's gone is left out. */
export function guessChips<S extends Character>(seg: Pick<Line, 'who_check'>, speakers: S[]): GuessChip<S>[] {
  const chips: GuessChip<S>[] = []
  for (const g of seg.who_check ?? []) {
    const speaker = speakers.find((s) => s.id === g.speaker_id)
    if (!speaker || chips.some((c) => c.speaker.id === speaker.id)) continue
    chips.push({ speaker, basis: basisOf(g.basis), key: chips.length === 0 ? 1 : 2 })
    if (chips.length === 2) break
  }
  return chips
}

/** A character's name as the editor shows it. */
export const characterName = (s: Pick<Character, 'display_name' | 'label'>) => s.display_name || s.label

const byName = (a: Character, b: Character) =>
  characterName(a).localeCompare(characterName(b), undefined, { numeric: true, sensitivity: 'base' })

/** Every character of the project to pick from: this episode's first, then the others, each once, by name. */
export function characterChoices<S extends Character>(
  episodeSegments: Pick<Segment, 'speaker_id'>[],
  projectSpeakers: S[],
): { speaker: S; inEpisode: boolean }[] {
  const inEpisode = new Set(episodeSegments.map((s) => s.speaker_id).filter(Boolean))
  const seen = new Set<string>()
  const unique = projectSpeakers.filter((s) => !seen.has(s.id) && !!seen.add(s.id))
  const here = unique.filter((s) => inEpisode.has(s.id)).sort(byName)
  const rest = unique.filter((s) => !inEpisode.has(s.id)).sort(byName)
  return [...here.map((speaker) => ({ speaker, inEpisode: true })), ...rest.map((speaker) => ({ speaker, inEpisode: false }))]
}

const copyChecks = (c: WhoGuess[] | null | undefined): WhoGuess[] | null =>
  c == null ? null : c.map((g) => ({ speaker_id: g.speaker_id, basis: g.basis }))

/**
 * A pick's undo and redo as PATCH bodies. Undo sends the speaker, the check and
 * whether it was confirmed together — a speaker change alone confirms the line,
 * which would let a guess teach a profile. When the pick rewrote the Khmer, undo
 * puts it back as machine Khmer (so it still counts as unedited) on the track it
 * had; otherwise the Khmer isn't touched. Redo replays the pick's result.
 */
export function pickUndoPatches(
  before: Pick<Segment, 'speaker_id' | 'who_check' | 'who_confirmed' | 'lane_index'>,
  picked: CharacterPicked,
): { before: SegmentUpdate; after: SegmentUpdate } {
  const was: SegmentUpdate = {
    speaker_id: before.speaker_id ?? null,
    who_check: copyChecks(before.who_check),
    who_confirmed: !!before.who_confirmed,
  }
  const now: SegmentUpdate = { speaker_id: picked.segment.speaker_id ?? null, who_check: null, who_confirmed: true }
  if (picked.retranslated) {
    Object.assign(was, { khmer_text: picked.previous_khmer, khmer_machine: picked.previous_khmer, lane_index: before.lane_index ?? 0 })
    const khmer = picked.segment.khmer_text
    Object.assign(now, { khmer_text: khmer, khmer_machine: khmer, lane_index: picked.segment.lane_index ?? 0 })
  }
  return { before: was, after: now }
}

/** "Unassigned": no character, the check kept; undo gives the speaker back without confirming the line. */
export function unassignPatches(
  before: Pick<Segment, 'speaker_id' | 'who_check' | 'who_confirmed'>,
): { before: SegmentUpdate; after: SegmentUpdate } {
  return {
    before: { speaker_id: before.speaker_id ?? null, who_check: copyChecks(before.who_check), who_confirmed: !!before.who_confirmed },
    after: { speaker_id: null },
  }
}
