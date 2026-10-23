// src/lib/historyHelpers.ts
// Reusable undo/redo entry builders for the editor's mutation call sites —
// see historyStore.ts for the stack itself. Each helper captures a
// before/after patch and pushes an entry whose undo/redo both replay the
// same kind of API call the original edit used, then invalidate the query
// so the UI reflects the reverted/reapplied server state.

import type { QueryClient } from '@tanstack/react-query'
import { jobs as jobsApi, segments as segmentsApi, speakers as speakersApi } from '@/api/client'
import type { LayoutChange, Segment, SegmentCreate, SegmentUpdate, SpeakerMergeResponse, SpeakerUpdate, SplitResult } from '@/types'
import { invalidateSpeakerLines, putSegments } from '@/hooks/useApi'
import { useHistoryStore } from '@/store/historyStore'

export function recordSegmentChange(
  qc: QueryClient,
  jobId: string,
  segmentId: string,
  before: SegmentUpdate,
  after: SegmentUpdate,
  label: string
) {
  useHistoryStore.getState().push({
    label,
    undo: async () => {
      await segmentsApi.update(segmentId, before)
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    redo: async () => {
      await segmentsApi.update(segmentId, after)
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

// Undo of a delete re-creates the segment, which always gets a new
// server-generated id — the box keeps redo/undo cycling targeting whichever
// id is currently live instead of the one captured at record-time.
export function recordSegmentDelete(
  qc: QueryClient,
  jobId: string,
  deletedSegment: Segment,
  label = 'Delete segment'
) {
  const box = { currentId: deletedSegment.id }
  const snapshot: SegmentCreate = {
    speaker_id: deletedSegment.speaker_id ?? null,
    voice_id: deletedSegment.voice_id ?? null,
    lane_index: deletedSegment.lane_index ?? 0,
    start_time: deletedSegment.start_time,
    end_time: deletedSegment.end_time,
    source_text: deletedSegment.source_text,
    english_text: deletedSegment.english_text,
    khmer_text: deletedSegment.khmer_text,
    notes: deletedSegment.notes,
  }

  useHistoryStore.getState().push({
    label,
    undo: async () => {
      const created = await segmentsApi.create(jobId, snapshot)
      box.currentId = created.id
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    redo: async () => {
      await segmentsApi.delete(box.currentId)
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

// Deleting a multi-selection is ONE history entry: a single undo recreates
// every clip, a single redo deletes them again. Recreated clips get fresh
// server ids, so each is re-boxed exactly like recordSegmentDelete does.
export function recordSegmentsDelete(
  qc: QueryClient,
  jobId: string,
  deletedSegments: Segment[],
  label?: string
) {
  const items = deletedSegments.map((seg) => ({
    box: { currentId: seg.id },
    snapshot: {
      speaker_id: seg.speaker_id ?? null,
      voice_id: seg.voice_id ?? null,
      lane_index: seg.lane_index ?? 0,
      start_time: seg.start_time,
      end_time: seg.end_time,
      source_text: seg.source_text,
      english_text: seg.english_text,
      khmer_text: seg.khmer_text,
      notes: seg.notes,
    } satisfies SegmentCreate,
  }))

  useHistoryStore.getState().push({
    label: label ?? `Delete ${deletedSegments.length} clips`,
    undo: async () => {
      for (const it of items) {
        const created = await segmentsApi.create(jobId, it.snapshot)
        it.box.currentId = created.id
      }
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    redo: async () => {
      for (const it of items) {
        await segmentsApi.delete(it.box.currentId)
      }
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

// Moving a multi-selection is ONE history entry: a single undo restores every
// clip's previous position, a single redo re-applies the whole move.
export function recordSegmentsChange(
  qc: QueryClient,
  jobId: string,
  changes: { id: string; before: SegmentUpdate; after: SegmentUpdate }[],
  label?: string
) {
  useHistoryStore.getState().push({
    label: label ?? `Move ${changes.length} clips`,
    undo: async () => {
      for (const c of changes) await segmentsApi.update(c.id, c.before)
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    redo: async () => {
      for (const c of changes) await segmentsApi.update(c.id, c.after)
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

export function recordSegmentCreate(
  qc: QueryClient,
  jobId: string,
  createdSegment: Segment,
  label = 'Add segment'
) {
  const box = { currentId: createdSegment.id }
  const snapshot: SegmentCreate = {
    speaker_id: createdSegment.speaker_id ?? null,
    voice_id: createdSegment.voice_id ?? null,
    lane_index: createdSegment.lane_index ?? 0,
    start_time: createdSegment.start_time,
    end_time: createdSegment.end_time,
    source_text: createdSegment.source_text,
    english_text: createdSegment.english_text,
    khmer_text: createdSegment.khmer_text,
    notes: createdSegment.notes,
  }

  useHistoryStore.getState().push({
    label,
    undo: async () => {
      await segmentsApi.delete(box.currentId)
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    redo: async () => {
      const created = await segmentsApi.create(jobId, snapshot)
      box.currentId = created.id
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

/**
 * A drop, trim or track change: undo and redo each send ONE layout request
 * with every clip's place before / after — including clips the server moved
 * to another track to make room — so one undo puts every clip back exactly.
 */
export function recordLayoutChange(
  qc: QueryClient,
  jobId: string,
  changes: { id: string; before: LayoutChange; after: LayoutChange }[],
  label: string,
) {
  if (!changes.length) return
  const send = async (side: 'before' | 'after') => {
    const updated = await segmentsApi.layout(jobId, changes.map((c) => c[side]))
    putSegments(qc, jobId, updated)
    qc.invalidateQueries({ queryKey: ['segments', jobId] })
  }
  useHistoryStore.getState().push({ label, undo: () => send('before'), redo: () => send('after') })
}

/**
 * A split is one server call (POST /segments/{id}/split): undo removes the
 * second half and gives the first back its text, length and track; redo splits
 * again at the same time. The second half gets a new id each time — boxed.
 */
export function recordSegmentSplit(
  qc: QueryClient,
  jobId: string,
  before: Segment,
  result: SplitResult,
  at: number,
  label = 'Split clip',
) {
  const box = { secondId: result.second.id }
  useHistoryStore.getState().push({
    label,
    undo: async () => {
      await segmentsApi.delete(box.secondId)
      await segmentsApi.update(before.id, {
        end_time: before.end_time,
        lane_index: before.lane_index ?? 0,
        khmer_text: before.khmer_text,
        source_text: before.source_text,
        english_text: before.english_text,
        source_start: before.source_start ?? null,
        source_end: before.source_end ?? null,
        voice_text: before.voice_text ?? null,
      })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    redo: async () => {
      const again = await segmentsApi.split(before.id, at)
      box.secondId = again.second.id
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

/** Duplicate (with its own copy of the voice): undo deletes the copy, redo copies again. */
export function recordSegmentDuplicate(qc: QueryClient, jobId: string, created: Segment, sourceId: string) {
  const box = { currentId: created.id }
  useHistoryStore.getState().push({
    label: 'Duplicate clip',
    undo: async () => {
      await segmentsApi.delete(box.currentId)
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    redo: async () => {
      box.currentId = (await segmentsApi.duplicate(sourceId)).id
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

/** The music dipping under the voices switched on or off: one undo step. */
export function recordDuckingChange(qc: QueryClient, jobId: string, before: boolean, after: boolean) {
  const send = async (on: boolean) => {
    qc.setQueryData(['job', jobId], await jobsApi.setDucking(jobId, on))
  }
  useHistoryStore.getState().push({
    label: after ? 'Music dips under voices' : 'Music stays level',
    undo: () => send(before),
    redo: () => send(after),
  })
}

export function recordSpeakerChange(
  qc: QueryClient,
  projectId: string,
  speakerId: string,
  before: SpeakerUpdate,
  after: SpeakerUpdate,
  label: string
) {
  useHistoryStore.getState().push({
    label,
    undo: async () => {
      await speakersApi.update(speakerId, before)
      qc.invalidateQueries({ queryKey: ['speakers', projectId] })
    },
    redo: async () => {
      await speakersApi.update(speakerId, after)
      qc.invalidateQueries({ queryKey: ['speakers', projectId] })
    },
  })
}

/**
 * Undo is one server step (POST /speakers/{into}/unmerge): the merged-away
 * speaker comes back as it was, with the moved lines still on `intoId` and its
 * Story characters. Redo merges the speaker that came back.
 */
export function recordSpeakerMerge(
  qc: QueryClient,
  projectId: string,
  merged: SpeakerMergeResponse,
  intoId: string,
) {
  const box = { merged }
  useHistoryStore.getState().push({
    label: 'Merge speakers',
    undo: async () => {
      const back = await speakersApi.unmerge(intoId, box.merged)
      box.merged = { ...box.merged, removed: back }
      invalidateSpeakerLines(qc, projectId)
    },
    redo: async () => {
      box.merged = await speakersApi.merge(box.merged.removed.id, intoId)
      invalidateSpeakerLines(qc, projectId)
    },
  })
}
