// src/lib/historyHelpers.ts
// Reusable undo/redo entry builders for the editor's mutation call sites —
// see historyStore.ts for the stack itself. Each helper captures a
// before/after patch and pushes an entry whose undo/redo both replay the
// same kind of API call the original edit used, then invalidate the query
// so the UI reflects the reverted/reapplied server state.

import type { QueryClient } from '@tanstack/react-query'
import { segments as segmentsApi, speakers as speakersApi } from '@/api/client'
import type { Segment, SegmentCreate, SegmentUpdate, SpeakerUpdate } from '@/types'
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

// Splitting is two API calls (shorten the original + create the tail), so its
// undo has to reverse both — a plain recordSegmentChange would leave the new
// clip orphaned on the timeline.
export function recordSegmentSplit(
  qc: QueryClient,
  jobId: string,
  originalId: string,
  originalEnd: number,
  splitTime: number,
  createdSegment: Segment,
  label = 'Split clip'
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
      await segmentsApi.update(originalId, { end_time: originalEnd })
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    redo: async () => {
      await segmentsApi.update(originalId, { end_time: splitTime })
      const created = await segmentsApi.create(jobId, snapshot)
      box.currentId = created.id
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
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
