// src/hooks/useApi.ts
// React Query hooks — data fetching, caching, polling

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projects, jobs, speakers, segments, tts, health } from '@/api/client'
import type { ProjectCreate, SpeakerUpdate, SegmentUpdate } from '@/types'

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

export function useDeleteProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => projects.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
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
    refetchInterval: (query) => {
      const data = query.state.data
      const status = data?.status
      const running = ['pending', 'uploading', 'extracting', 'diarizing', 'transcribing', 'translating', 'synthesizing', 'mixing']
      if (status && running.includes(status)) return 2000
      // Keep polling after completion until video_url is available
      if (status === 'completed' && !data?.video_url) return 3000
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
      qc.invalidateQueries({ queryKey: ['segments', updated.job_id] })
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

export function useMixFinalAudio() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ jobId, muteOriginal }: { jobId: string; muteOriginal?: boolean }) => 
      tts.mixFinalAudio(jobId, muteOriginal),
    onSuccess: (_, { jobId }) => {
      qc.invalidateQueries({ queryKey: ['job', jobId] })
    },
  })
}
