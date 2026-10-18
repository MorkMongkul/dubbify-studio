// src/hooks/useApi.ts
// React Query hooks — data fetching, caching, polling

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projects, jobs, speakers, segments, tts, health, voices, overlays, overlayTemplates, timelineActions, chat, appSettings } from '@/api/client'
import type { VoiceCreateInput } from '@/api/client'
import type {
  Segment, ProjectCreate, ProjectUpdate, SpeakerUpdate, SpeakerCreate, SegmentUpdate, SegmentCreate, Voice, OverlayUpdate, AppSettingsUpdate, GeminiModelSetting,
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
    mutationFn: ({ projectId, file }: { projectId: string; file: File }) =>
      jobs.upload(projectId, file),
    onSuccess: (_, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['jobs', projectId] })
    },
  })
}

export function useUploadWithSubtitle() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId, video, subtitle,
    }: { projectId: string; video: File; subtitle: File }) =>
      jobs.uploadWithSubtitle(projectId, video, subtitle),
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
      // Write the PATCH response straight into the cache instead of
      // invalidating — this fires after every drag/trim/edit, and refetching
      // the full segment list each time is a large, needless round trip.
      qc.setQueryData<Segment[]>(['segments', updated.job_id], (old) =>
        old ? old.map((s) => (s.id === updated.id ? updated : s)) : old
      )
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

// ── Analyze (Stage 2 trigger) ─────────────────────────────────
export function useAnalyzeJob() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, maxSpeakers }: { jobId: string; maxSpeakers?: number | null }) =>
      jobs.analyze(jobId, maxSpeakers),
    onSuccess: (_, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
  })
}

export function useTranslateMissing() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => jobs.translateMissing(jobId),
    onSuccess: (_, jobId) => {
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
export function useChatMessages(projectId: string | null) {
  return useQuery({
    queryKey: ['chat-messages', projectId],
    queryFn: () => chat.listMessages(projectId!),
    enabled: !!projectId,
  })
}

export function useSendChatMessage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ projectId, text }: { projectId: string; text: string }) =>
      chat.sendMessage(projectId, text),
    onSuccess: (_, { projectId }) => {
      qc.invalidateQueries({ queryKey: ['chat-messages', projectId] })
      qc.invalidateQueries({ queryKey: ['chat-pending-edits', projectId] })
      // A tool call may have applied an auto-mode edit straight to a segment.
      qc.invalidateQueries({ queryKey: ['segments'] })
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
    },
  })
}
