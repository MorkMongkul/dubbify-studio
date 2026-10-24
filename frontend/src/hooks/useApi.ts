// src/hooks/useApi.ts
// React Query hooks — data fetching, caching, polling

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { AxiosError } from 'axios'
import { projects, jobs, speakers, segments, tts, health, voices, overlays, overlayTemplates, timelineActions, chat, appSettings, story, stemPieces } from '@/api/client'
import type { VoiceCreateInput } from '@/api/client'
import { useEditorStore } from '@/store/editorStore'
import { standInMessage, withTurn } from '@/lib/chatMessages'
import type { StemPieceLists } from '@/lib/stemPieces'
import type {
  Segment, LayoutChange, Stem, StemPiece, ProjectCreate, ProjectUpdate, SpeakerUpdate, SpeakerCreate, SegmentUpdate, SegmentCreate, Voice, OverlayUpdate, AppSettingsUpdate, GeminiModelSetting,
  ChatLineRef, ChatMessage, StoryEdit, TrackFlagMap,
} from '@/types'

// ── Health ────────────────────────────────────────────────────
export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: health.check,
    refetchInterval: 30000,
    staleTime: 10000,
  })
}

// ── Projects ──────────────────────────────────────────────────
export function useProjects() {
  return useQuery({
    queryKey: ['projects'],
    queryFn: projects.list,
    staleTime: 5000,
  })
}

export function useProject(id: string | null) {
  return useQuery({
    queryKey: ['project', id],
    queryFn: () => projects.get(id!),
    enabled: !!id,
  })
}

export function useCreateProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ProjectCreate) => projects.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })
}

export function useUpdateProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ProjectUpdate }) =>
      projects.update(id, data),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ['project', updated.id] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

export function useDeleteProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => projects.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })
}

export function useUploadProjectLogo() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ projectId, file }: { projectId: string; file: File }) =>
      projects.uploadLogo(projectId, file),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ['project', updated.id] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

export function useDeleteProjectLogo() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (projectId: string) => projects.deleteLogo(projectId),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ['project', updated.id] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

// ── Jobs ──────────────────────────────────────────────────────
export function useProjectJobs(projectId: string | null) {
  return useQuery({
    queryKey: ['jobs', projectId],
    queryFn: () => jobs.listByProject(projectId!),
    enabled: !!projectId,
    staleTime: 3000,
  })
}

export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => jobs.get(jobId!),
    enabled: !!jobId,
    // Keep polling even when the tab is in the background (e.g. while watching
    // the backend logs) and refetch on return, so progress never appears frozen.
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => {
      const data = query.state.data
      const status = data?.status
      // Active pipeline stages — poll every 2s
      const running = ['pending', 'extracting', 'separating', 'diarizing', 'transcribing', 'translating', 'synthesizing', 'mixing']
      if (status && running.includes(status)) return 2000
      // stems_ready: paused, waiting for user — poll slowly to pick up any changes
      if (status === 'stems_ready') return 5000
      // completed / failed are terminal — TTS + mix are user-triggered and their
      // mutations invalidate this query, so no need to keep polling.
      return false
    },
  })
}

export function useUploadVideo() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ projectId, file, onProgress }: { projectId: string; file: File; onProgress?: (fraction: number) => void }) =>
      jobs.upload(projectId, file, onProgress),
    onSuccess: (_, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['jobs', projectId] })
    },
  })
}

export function useUploadWithSubtitle() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId, video, subtitle, onProgress,
    }: { projectId: string; video: File; subtitle: File; onProgress?: (fraction: number) => void }) =>
      jobs.uploadWithSubtitle(projectId, video, subtitle, onProgress),
    onSuccess: (_, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['jobs', projectId] })
    },
  })
}

export function useDeleteJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId }: { jobId: string; projectId: string }) =>
      jobs.delete(jobId),
    onSuccess: (_, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['jobs', projectId] })
    },
  })
}

// ── Subtitle Tracks ───────────────────────────────────────────
export function useSubtitleTracks(jobId: string | null) {
  return useQuery({
    queryKey: ['subtitle-tracks', jobId],
    queryFn: () => jobs.getSubtitleTracks(jobId!),
    enabled: !!jobId,
  })
}

// ── Speakers ──────────────────────────────────────────────────
export function useSpeakers(projectId: string | null) {
  return useQuery({
    queryKey: ['speakers', projectId],
    queryFn: () => speakers.listByProject(projectId!),
    enabled: !!projectId,
    staleTime: 10000,
  })
}

export function useUpdateSpeaker() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ speakerId, data }: { speakerId: string; data: SpeakerUpdate }) =>
      speakers.update(speakerId, data),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ['speakers', updated.project_id] })
    },
  })
}

/** Lines moved between speakers in any episode, and the Story character may have followed. */
export function invalidateSpeakerLines(qc: ReturnType<typeof useQueryClient>, projectId: string) {
  qc.invalidateQueries({ queryKey: ['speakers', projectId] })
  qc.invalidateQueries({ queryKey: ['segments'] })
  qc.invalidateQueries({ queryKey: ['story', projectId] })
}

export function useMergeSpeaker() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ speakerId, into }: { speakerId: string; into: string; projectId: string }) =>
      speakers.merge(speakerId, into),
    onSuccess: (_result, { projectId }) => invalidateSpeakerLines(qc, projectId),
  })
}

export function useCreateSpeaker() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ projectId, data }: { projectId: string; data: SpeakerCreate }) =>
      speakers.create(projectId, data),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ['speakers', created.project_id] })
    },
  })
}

// ── Segments ──────────────────────────────────────────────────
export function useSegments(jobId: string | null) {
  return useQuery({
    queryKey: ['segments', jobId],
    queryFn: () => segments.listByJob(jobId!),
    enabled: !!jobId,
    staleTime: 5000,
  })
}

export function useUpdateSegment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ segmentId, data }: { segmentId: string; data: SegmentUpdate }) =>
      segments.update(segmentId, data),
    onSuccess: (updated) => {
      // The PATCH response goes straight into the cache (the edited line shows
      // at once); the list is then fetched again because the server may have
      // moved other clips to another track to make room (timeline.settle).
      putSegments(qc, updated.job_id, [updated])
      qc.invalidateQueries({ queryKey: ['segments', updated.job_id] })
    },
  })
}

/** Write segments the server returned into the job's cached list. */
export function putSegments(qc: ReturnType<typeof useQueryClient>, jobId: string, updated: Segment[]) {
  const byId = new Map(updated.map((s) => [s.id, s]))
  qc.setQueryData<Segment[]>(['segments', jobId], (old) => {
    if (!old) return old
    const known = new Set(old.map((s) => s.id))
    const merged = old.map((s) => byId.get(s.id) ?? s)
    for (const s of updated) if (!known.has(s.id)) merged.push(s)
    return merged.sort((a, b) => a.start_time - b.start_time)
  })
}

/**
 * Move / trim / re-track clips in one request (a drop, a trim, a group move).
 * The changes show at once (editorStore.setPendingLayout) and give way to the
 * server's answer — which includes clips it moved to make room — success or not.
 */
export function useLayoutSegments() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, changes, trackFlags }: { jobId: string; changes: LayoutChange[]; trackFlags?: TrackFlagMap }) =>
      segments.layout(jobId, changes, trackFlags),
    onMutate: ({ changes }) => useEditorStore.getState().setPendingLayout(changes),
    onSuccess: (updated, { jobId, trackFlags }) => {
      putSegments(qc, jobId, updated)
      if (trackFlags) qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
    onError: (_, { jobId }) => qc.invalidateQueries({ queryKey: ['segments', jobId] }),
    onSettled: (_, __, { changes }) => useEditorStore.getState().clearPendingLayout(changes.map((c) => c.id)),
  })
}

export function useSplitSegment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ segmentId, at }: { segmentId: string; at: number; jobId: string }) => segments.split(segmentId, at),
    onSuccess: (result, { jobId }) => {
      putSegments(qc, jobId, [result.first, result.second])
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

export function useDuplicateSegment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ segmentId }: { segmentId: string; jobId: string }) => segments.duplicate(segmentId),
    onSuccess: (created, { jobId }) => {
      putSegments(qc, jobId, [created])
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

export function useCreateSegment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, data }: { jobId: string; data: SegmentCreate }) =>
      segments.create(jobId, data),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ['segments', created.job_id] })
    },
  })
}

export function useDeleteSegment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ segmentId }: { segmentId: string; jobId: string }) =>
      segments.delete(segmentId),
    onSuccess: (_, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

export function useApproveSegment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (segmentId: string) => segments.approve(segmentId),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: ['segments', updated.job_id] })
    },
  })
}

export function useApproveAll() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => segments.approveAll(jobId),
    onSuccess: (_, jobId) => {
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

export function useSetBgmVolume() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, volumeDb }: { jobId: string; volumeDb: number }) =>
      jobs.setBgmVolume(jobId, volumeDb),
    onSuccess: (updated) => {
      qc.setQueryData(['job', updated.id], updated)
    },
  })
}

export function useSetTrackFlags() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, flags }: { jobId: string; flags: TrackFlagMap }) => jobs.setTrackFlags(jobId, flags),
    onSuccess: (updated) => {
      qc.setQueryData(['job', updated.id], updated)
    },
  })
}

export function useSetDucking() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, on }: { jobId: string; on: boolean }) => jobs.setDucking(jobId, on),
    onSuccess: (updated) => {
      qc.setQueryData(['job', updated.id], updated)
    },
  })
}

// ── The music and the original voices in pieces (0.3.2) ──────
export function useStemPieces(jobId: string | null | undefined) {
  return useQuery({
    queryKey: ['stem-pieces', jobId],
    queryFn: () => stemPieces.list(jobId!),
    enabled: !!jobId,
  })
}

/** Replace a stem's pieces: shown at once, then what the server stored. */
export function useSetStemPieces() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, stem, pieces }: { jobId: string; stem: Stem; pieces: StemPiece[] }) =>
      stemPieces.put(jobId, stem, pieces),
    onMutate: ({ jobId, stem, pieces }) => {
      qc.setQueryData<StemPieceLists>(['stem-pieces', jobId], (old) => old && { ...old, [stem]: pieces })
    },
    onSuccess: (stored, { jobId, stem }) => {
      qc.setQueryData<StemPieceLists>(['stem-pieces', jobId], (old) => old && { ...old, [stem]: stored })
    },
    onError: (_, { jobId }) => qc.invalidateQueries({ queryKey: ['stem-pieces', jobId] }),
  })
}

/** The picture strip: asked again every few seconds until it's ready. */
export function useFrames(jobId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: ['frames', jobId],
    queryFn: () => jobs.frames(jobId!),
    enabled: !!jobId && enabled,
    refetchInterval: (query) => (query.state.data?.ready ? false : 4000),
    staleTime: Infinity,
  })
}

export function useSetJobTitle() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, title }: { jobId: string; title: string }) =>
      jobs.setTitle(jobId, title),
    onSuccess: (updated) => {
      qc.setQueryData(['job', updated.id], updated)
      qc.invalidateQueries({ queryKey: ['jobs', updated.project_id] })
    },
  })
}

// ── Story notes ───────────────────────────────────────────────
export function useStory(projectId: string | null) {
  return useQuery({
    queryKey: ['story', projectId],
    queryFn: () => story.get(projectId!),
    enabled: !!projectId,
    // While it learns from episodes, poll for its progress and what it learned
    refetchInterval: (query) => (query.state.data?.learning.state === 'running' ? 2000 : false),
  })
}

export function useUpdateStory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ projectId, edit }: { projectId: string; edit: StoryEdit }) => story.update(projectId, edit),
    onSuccess: (view, { projectId }) => qc.setQueryData(['story', projectId], view),
  })
}

export function useLearnStory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (projectId: string) => story.learn(projectId),
    onSuccess: (view, projectId) => qc.setQueryData(['story', projectId], view),
  })
}

/** Finishing an episode starts learning from it (the story notes poll while that runs). */
export function useSetEpisodeFinished() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, finished }: { jobId: string; finished: boolean }) => jobs.setFinished(jobId, finished),
    onSuccess: (updated) => {
      qc.setQueryData(['job', updated.id], updated)
      qc.invalidateQueries({ queryKey: ['jobs', updated.project_id] })
      qc.invalidateQueries({ queryKey: ['story', updated.project_id] })
    },
  })
}

// ── Analyze (Stage 2 trigger) ─────────────────────────────────
export function useAnalyzeJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, maxSpeakers }: { jobId: string; maxSpeakers?: number | null }) =>
      jobs.analyze(jobId, maxSpeakers),
    onSuccess: (_, { jobId }) => {
      // Remembered so the progress can name what's running (lib/jobProgress.ts)
      useEditorStore.getState().setJobAction(jobId, 'analyze')
      qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
  })
}

export function useTranslateMissing() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => jobs.translateMissing(jobId),
    onSuccess: (_, jobId) => {
      useEditorStore.getState().setJobAction(jobId, 'translate-missing')
      qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
  })
}

export function useRetrySeparation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => jobs.retrySeparation(jobId),
    onSuccess: (_, jobId) => {
      qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
  })
}

export function useDetectSpeakers() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => jobs.detectSpeakers(jobId),
    onSuccess: (_, jobId) => {
      useEditorStore.getState().setJobAction(jobId, 'detect-speakers')
      qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
  })
}

// ── TTS ───────────────────────────────────────────────────────
export function useSynthesizeJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => tts.synthesizeJob(jobId),
    onSuccess: (_, jobId) => {
      qc.invalidateQueries({ queryKey: ['job', jobId] })
      qc.invalidateQueries({ queryKey: ['speakers'] })   // speakers without a voice get one picked
    },
  })
}

export function useSynthesizeSegment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ segmentId }: { segmentId: string; jobId: string }) =>
      tts.synthesizeSegment(segmentId),
    onSuccess: (_, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
    // A speaker without a voice gets one picked before the voice server is
    // asked, so the pick is there even when the server then fails
    onSettled: () => qc.invalidateQueries({ queryKey: ['speakers'] }),
  })
}

export function useSynthesizeBatch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ segmentIds }: { segmentIds: string[]; jobId: string }) =>
      tts.synthesizeBatch(segmentIds),
    onSuccess: (_, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
    },
  })
}

export function useMixFinalAudio() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, muteOriginal, exportPath }: { jobId: string; muteOriginal?: boolean; exportPath?: string }) =>
      tts.mixFinalAudio(jobId, muteOriginal, exportPath),
    onSuccess: (_, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
  })
}

// ── Bulk timeline actions ───────────────────────────────────────
export function useAutofitSegments() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => timelineActions.autofit(jobId),
    onSuccess: (_, jobId) => qc.invalidateQueries({ queryKey: ['segments', jobId] }),
  })
}

export function useTidyLanes() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => timelineActions.tidyLanes(jobId),
    onSuccess: (_, jobId) => qc.invalidateQueries({ queryKey: ['segments', jobId] }),
  })
}

// ── Overlays (draggable/resizable canvas layers) ────────────────
export function useOverlays(jobId: string | null) {
  return useQuery({
    queryKey: ['overlays', jobId],
    queryFn: () => overlays.listByJob(jobId!),
    enabled: !!jobId,
  })
}

export function useCreateImageOverlay() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, file }: { jobId: string; file: File }) =>
      overlays.createImage(jobId, file),
    onSuccess: (created) => qc.invalidateQueries({ queryKey: ['overlays', created.job_id] }),
  })
}

export function useCreateOverlayFromLogo() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => overlays.createFromProjectLogo(jobId),
    onSuccess: (created) => qc.invalidateQueries({ queryKey: ['overlays', created.job_id] }),
  })
}

export function useCreateSubtitleOverlay() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => overlays.createSubtitle(jobId),
    onSuccess: (created) => qc.invalidateQueries({ queryKey: ['overlays', created.job_id] }),
  })
}

export function useCreateShapeOverlay() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, blur }: { jobId: string; blur?: boolean }) => overlays.createShape(jobId, { blur }),
    onSuccess: (created) => qc.invalidateQueries({ queryKey: ['overlays', created.job_id] }),
  })
}

export function useUpdateOverlay() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; jobId: string; data: OverlayUpdate }) =>
      overlays.update(id, data),
    onSuccess: (_, { jobId }) => qc.invalidateQueries({ queryKey: ['overlays', jobId] }),
  })
}

export function useDeleteOverlay() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id }: { id: string; jobId: string }) => overlays.delete(id),
    onSuccess: (_, { jobId }) => qc.invalidateQueries({ queryKey: ['overlays', jobId] }),
  })
}

// ── Overlay templates (global "brand kit") ──────────────────────
export function useOverlayTemplates() {
  return useQuery({
    queryKey: ['overlay-templates'],
    queryFn: () => overlayTemplates.list(),
  })
}

export function useCreateOverlayTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, fromJobId, setDefault }: { name: string; fromJobId: string; setDefault?: boolean }) =>
      overlayTemplates.create(name, fromJobId, setDefault),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['overlay-templates'] }),
  })
}

export function useUpdateOverlayTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: { name?: string; is_default?: boolean } }) =>
      overlayTemplates.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['overlay-templates'] }),
  })
}

export function useDeleteOverlayTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => overlayTemplates.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['overlay-templates'] }),
  })
}

export function useApplyOverlayTemplate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, jobId }: { id: string; jobId: string }) =>
      overlayTemplates.applyToJob(id, jobId),
    onSuccess: (_, { jobId }) => qc.invalidateQueries({ queryKey: ['overlays', jobId] }),
  })
}

// ── Voices (Voice Creator) ────────────────────────────────────
export function useVoices() {
  return useQuery({
    queryKey: ['voices'],
    queryFn: voices.list,
    staleTime: 10000,
  })
}

export function useCreateVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: VoiceCreateInput) => voices.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['voices'] }),
  })
}

export function useUpdateVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Voice> }) =>
      voices.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['voices'] }),
  })
}

export function useDeleteVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => voices.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['voices'] }),
  })
}

export function useCopyVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => voices.copy(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['voices'] }),
  })
}

export function usePreviewVoice() {
  return useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      voices.preview(id, text),
  })
}

// ── Assistant chat ────────────────────────────────────────────
export function useChatThreads(projectId: string | null) {
  return useQuery({
    queryKey: ['chat-threads', projectId],
    queryFn: () => chat.listThreads(projectId!),
    enabled: !!projectId,
  })
}

/** Where a chat's messages live in the cache; a new chat (no id yet) keeps its stand-in under the project. */
export const chatMessagesKey = (projectId: string, threadId: string | null) =>
  ['chat-messages', threadId ?? `new:${projectId}`]

export function useChatMessages(projectId: string | null, threadId: string | null) {
  return useQuery({
    queryKey: chatMessagesKey(projectId ?? '', threadId),
    queryFn: () => chat.listMessages(threadId!),
    enabled: !!projectId && !!threadId,
  })
}

export function useDeleteChatThread() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ threadId }: { threadId: string; projectId: string }) => chat.deleteThread(threadId),
    onSuccess: (_, { threadId, projectId }) => {
      qc.removeQueries({ queryKey: ['chat-messages', threadId] })
      qc.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      qc.invalidateQueries({ queryKey: ['chat-pending-edits', projectId] })
    },
  })
}

/** Sending runs as `['chat-send']`, so the panel can tell a turn is running
 *  even after it was closed and opened again (useIsMutating). */
export function useSendChatMessage() {
  const qc = useQueryClient()
  return useMutation({
    mutationKey: ['chat-send'],
    // `threadId`: the chat (null starts one, named after this message);
    // `line`: the line being edited, which "this line" means (ChatLineRef snapshot)
    mutationFn: ({ projectId, threadId, text, line }: {
      projectId: string; threadId: string | null; text: string; line?: ChatLineRef
    }) => chat.sendMessage(projectId, { text, threadId, lineId: line?.segment_id }),
    // What you sent shows at once; the saved turn (your message and the reply)
    // takes its place when the Assistant answers, which can take a minute.
    onMutate: async ({ projectId, threadId, text, line }) => {
      const key = chatMessagesKey(projectId, threadId)
      await qc.cancelQueries({ queryKey: key })
      const sent = standInMessage(projectId, text, new Date(), line)
      qc.setQueryData<ChatMessage[]>(key, (list = []) => [...list, sent])
      return { standInId: sent.id }
    },
    onSuccess: (data, { projectId, threadId }, sending) => {
      const from = chatMessagesKey(projectId, threadId)
      const list = qc.getQueryData<ChatMessage[]>(from) ?? []
      qc.setQueryData<ChatMessage[]>(['chat-messages', data.thread_id], withTurn(list, sending?.standInId ?? '', data.messages))
      if (!threadId) {
        // The new chat has its id now: its messages move there, and it stays
        // open unless the user went to another chat meanwhile
        qc.removeQueries({ queryKey: from })
        const { assistantThreads, setAssistantThread } = useEditorStore.getState()
        if (!assistantThreads[projectId]) setAssistantThread(projectId, data.thread_id)
      }
      qc.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      qc.invalidateQueries({ queryKey: ['chat-pending-edits', projectId] })
      // A tool call may have applied an auto-mode edit straight to a segment.
      qc.invalidateQueries({ queryKey: ['segments'] })
    },
    onError: (err, { projectId, threadId }, sending) => {
      if (sending) {
        qc.setQueryData<ChatMessage[]>(chatMessagesKey(projectId, threadId), (list = []) => withTurn(list, sending.standInId, []))
      }
      // A new chat's first message was saved before the AI failed: open that
      // chat, so sending again continues it instead of starting another
      const savedIn = (err as AxiosError<{ thread_id?: string }>).response?.data?.thread_id
      if (!threadId && savedIn) {
        const { assistantThreads, setAssistantThread } = useEditorStore.getState()
        if (!assistantThreads[projectId]) setAssistantThread(projectId, savedIn)
        qc.invalidateQueries({ queryKey: ['chat-messages', savedIn] })
      }
      qc.invalidateQueries({ queryKey: ['chat-threads', projectId] })
    },
    // The server keeps what was sent even when the Assistant fails
    onSettled: (data, _err, { threadId }) => {
      const saved = data?.thread_id ?? threadId
      if (saved) qc.invalidateQueries({ queryKey: ['chat-messages', saved] })
    },
  })
}

export function usePendingEdits(projectId: string | null, status: 'pending' | 'all' = 'pending') {
  return useQuery({
    queryKey: ['chat-pending-edits', projectId, status],
    queryFn: () => chat.listPendingEdits(projectId!, status),
    enabled: !!projectId,
  })
}

export function useApproveEdit() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ editId }: { editId: string; projectId: string }) => chat.approveEdit(editId),
    onSuccess: (_, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['chat-pending-edits', projectId] })
      qc.invalidateQueries({ queryKey: ['segments'] })
    },
  })
}

export function useRejectEdit() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ editId }: { editId: string; projectId: string }) => chat.rejectEdit(editId),
    onSuccess: (_, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['chat-pending-edits', projectId] })
    },
  })
}

// ── App settings (desktop Settings panel) ─────────────────────
export function useAppSettings() {
  return useQuery({ queryKey: ['app-settings'], queryFn: appSettings.get })
}

/** Gemini models the saved key can use — only fetched once a key is saved. */
export function useGeminiModels(enabled: boolean) {
  return useQuery({
    queryKey: ['gemini-models'],
    queryFn: appSettings.models,
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export function useTestGeminiModel() {
  return useMutation({
    mutationFn: ({ setting, model }: { setting: GeminiModelSetting; model: string }) =>
      appSettings.testModel(setting, model),
  })
}

export function useUpdateAppSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: AppSettingsUpdate) => appSettings.update(data),
    onSuccess: (data) => {
      qc.setQueryData(['app-settings'], data)
      qc.invalidateQueries({ queryKey: ['gemini-models'] })   // a new key or choice changes the list
      qc.invalidateQueries({ queryKey: ['story'] })           // the Story panel says whether it can learn
    },
  })
}
