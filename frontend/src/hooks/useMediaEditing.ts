// src/hooks/useMediaEditing.ts
// Editing an episode's media clips (0.4.0): what the media tracks, their
// right-click menu and the timeline's shortcuts (⌘C ⌘V ⌘D S ⌫) do to them.
// Every change sends the episode's whole list (one request) and is one undo
// step; a clip on a locked track is never changed.
import { useCallback, useMemo } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import type { AxiosError } from 'axios'
import type { MediaAsset, MediaClip, MediaClipList, MediaTrackFlagMap, MediaTrackSwitches } from '@/types'
import { useMediaAssets, useMediaClips, useSetMediaClips } from '@/hooks/useApi'
import { recordMediaChange } from '@/lib/historyHelpers'
import { MIN_CLIP_SECS, defaultClip, dropNewClip, duplicateClip, mediaSwitchesOf, pasteClips, splitClip, withMediaSwitch } from '@/lib/media'
import type { DropSpot } from '@/lib/tracks'
import { useEditorStore } from '@/store/editorStore'

export interface MediaEditing {
  list: MediaClipList | undefined
  assets: Map<string, MediaAsset>
  isLocked: (clip: MediaClip) => boolean
  /** The episode's clips (and track switches) become these: one request, one undo step. */
  commit: (clips: MediaClip[], label: string, flags?: MediaTrackFlagMap) => void
  /** ⌘C: copies the selected clips; false when none is selected. */
  copy: () => boolean
  /** ⌘V: pastes the copied clips at the playhead; false when nothing was copied. */
  paste: () => boolean
  duplicate: () => void
  /** Splits the selected clips (or `ids`) at `at`, else at the playhead. */
  split: (at?: number, ids?: string[]) => void
  remove: () => void
  /** A tile from the Media tab dropped at `at` on `spot`. */
  dropTile: (asset: MediaAsset, at: number, spot: DropSpot) => void
  toggleSwitch: (lane: number, key: keyof MediaTrackSwitches) => void
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
const errorText = (e: unknown, fallback: string) =>
  ((e as AxiosError<{ detail?: string }>)?.response?.data?.detail) || fallback
export const mediaLockedNotice = () => toast.info('This track is locked. Unlock it to edit its clips.')
const lockedIn = (l: MediaClipList, c: MediaClip) => mediaSwitchesOf(l.track_flags, c.lane_index).locked
const SWITCH_LABELS: Record<keyof MediaTrackSwitches, [string, string]> = {
  locked: ['Lock track', 'Unlock track'],
  hidden: ['Hide track', 'Show track'],
  muted: ['Mute track', 'Unmute track'],
}

export function useMediaEditing(jobId: string | undefined, projectId: string | undefined, duration: number): MediaEditing {
  const qc = useQueryClient()
  const { data: list } = useMediaClips(jobId)
  const { data: assetList } = useMediaAssets(projectId)
  // mutateAsync: TanStack calls mutate's own callbacks for the last call only, so the first
  // of two quick edits lost its undo step
  const { mutateAsync: setClips } = useSetMediaClips()
  const assets = useMemo(() => new Map((assetList ?? []).map((a) => [a.id, a])), [assetList])

  // The cache holds the latest list (an edit shows at once), so callbacks read it there
  const current = useCallback(
    () => (jobId ? qc.getQueryData<MediaClipList>(['media-clips', jobId]) : undefined), [qc, jobId])

  const commit = useCallback((clips: MediaClip[], label: string, flags?: MediaTrackFlagMap) => {
    const before = current()
    if (!jobId || !before) return
    setClips({ jobId, clips, trackFlags: flags ?? before.track_flags })
      .then((stored) => recordMediaChange(qc, jobId, before, stored, label))
      .catch((e) => toast.error(errorText(e, `Couldn't ${label.toLowerCase()}`)))
  }, [current, jobId, qc, setClips])

  // The selected clips that can be edited (a locked track's say so)
  const editableSelection = useCallback((l: MediaClipList, ids = useEditorStore.getState().selectedMediaIds) => {
    const picked = l.clips.filter((c) => ids.includes(c.id))
    const free = picked.filter((c) => !lockedIn(l, c))
    if (picked.length && !free.length) mediaLockedNotice()
    return free
  }, [])

  const copy = useCallback(() => {
    const l = current()
    const ids = useEditorStore.getState().selectedMediaIds
    const picked = l?.clips.filter((c) => ids.includes(c.id)) ?? []
    if (!picked.length) return false
    useEditorStore.getState().setMediaClipboard(picked)
    toast.success(`Copied ${plural(picked.length, 'clip')}`)
    return true
  }, [current])

  const paste = useCallback(() => {
    const l = current()
    const board = useEditorStore.getState().mediaClipboard
    if (!l || !board.length) return false
    const known = new Set((qc.getQueryData<MediaAsset[]>(['media', projectId]) ?? []).map((a) => a.id))
    const usable = board.filter((c) => known.has(c.asset_id))
    if (!usable.length) {
      toast.error("The copied clips' media isn't in this project")
      return true
    }
    const locked = new Set(Object.keys(l.track_flags).map(Number).filter((lane) => mediaSwitchesOf(l.track_flags, lane).locked))
    const made = pasteClips(usable, useEditorStore.getState().currentTime, l.clips, locked)
    commit([...l.clips, ...made], 'Paste')
    useEditorStore.getState().setSelectedMedia(made.map((c) => c.id))
    return true
  }, [current, qc, projectId, commit])

  const duplicate = useCallback(() => {
    const l = current()
    if (!l) return
    const picked = editableSelection(l)
    if (!picked.length) return
    const all = [...l.clips]
    const made: MediaClip[] = []
    for (const c of [...picked].sort((a, b) => a.start_time - b.start_time)) {
      const copy = duplicateClip(c, all)
      all.push(copy)
      made.push(copy)
    }
    commit(all, 'Duplicate')
    useEditorStore.getState().setSelectedMedia(made.map((c) => c.id))
  }, [current, editableSelection, commit])

  const split = useCallback((at?: number, ids?: string[]) => {
    const l = current()
    if (!l) return
    const t = at ?? useEditorStore.getState().currentTime
    const picked = editableSelection(l, ids)
    if (!picked.length) return
    const cut = picked.filter((c) => t >= c.start_time + MIN_CLIP_SECS && t <= c.end_time - MIN_CLIP_SECS)
    if (!cut.length) {
      toast.error('Move the playhead inside the clip to split it')
      return
    }
    commit(l.clips.flatMap((c) => (cut.includes(c) ? splitClip(c, t, assets.get(c.asset_id)?.kind ?? 'image') : [c])), 'Split')
  }, [current, editableSelection, commit, assets])

  const remove = useCallback(() => {
    const l = current()
    if (!l) return
    const picked = new Set(editableSelection(l).map((c) => c.id))
    if (!picked.size) return
    commit(l.clips.filter((c) => !picked.has(c.id)), 'Delete')
    useEditorStore.getState().setSelectedMedia([])
  }, [current, editableSelection, commit])

  const dropTile = useCallback((asset: MediaAsset, at: number, spot: DropSpot) => {
    const l = current()
    if (!l || asset.status !== 'ready') return
    const frame = useEditorStore.getState().videoSize ?? { width: asset.width, height: asset.height }
    const clip = defaultClip(asset, at, duration, 0, frame)
    const placed = dropNewClip(l.clips, clip, spot, l.track_flags, duration)
    commit(placed.clips, `Add ${asset.name}`, placed.track_flags)
    useEditorStore.getState().setSelectedMedia([clip.id])
  }, [current, duration, commit])

  const toggleSwitch = useCallback((lane: number, key: keyof MediaTrackSwitches) => {
    const l = current()
    if (!l) return
    const on = !mediaSwitchesOf(l.track_flags, lane)[key]
    commit(l.clips, SWITCH_LABELS[key][on ? 0 : 1], withMediaSwitch(l.track_flags, lane, key, on))
  }, [current, commit])

  const isLocked = useCallback((c: MediaClip) => !!list && lockedIn(list, c), [list])

  return useMemo(() => ({
    list, assets, isLocked, commit, copy, paste, duplicate, split, remove, dropTile, toggleSwitch,
  }), [list, assets, isLocked, commit, copy, paste, duplicate, split, remove, dropTile, toggleSwitch])
}
