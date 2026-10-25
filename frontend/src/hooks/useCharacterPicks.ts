// src/hooks/useCharacterPicks.ts
// Picking who speaks a line ("Who is this?", the line's speaker list): one
// request that confirms the line and may rewrite its Khmer for the new speaker
// (it waits for Gemini — a few seconds), one undo step, keys 1 / 2.
//
// A line with a pick on its way takes no second pick until it settles. A pick
// made from the "Who is this?" row (a chip, 1 / 2, Someone else…) moves on to
// the next line still to check at once, so 1 / 2 and Space go line to line
// while the Khmer is rewritten; a pick from the speaker list stays put.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import type { Segment, Speaker } from '@/types'
import { segments as segmentsApi } from '@/api/client'
import { usePickCharacter, putSegments } from '@/hooks/useApi'
import { useEditorStore } from '@/store/editorStore'
import { recordCharacterPick } from '@/lib/historyHelpers'
import { characterName, guessChips, isToCheck, nextToCheck, pickUndoPatches, unassignPatches } from '@/lib/whoCheck'
import { isTypingTarget } from '@/lib/transport'

export type PickFrom = 'chip' | 'menu' | 'select'
export interface PendingPick { speakerId: string; from: PickFrom }

interface Options {
  jobId: string
  segments: Segment[]
  speakers: Speaker[]
  /** Go to a line to check: select it for 1 / 2, show it, move the playhead there. */
  goTo: (seg: Segment) => void
  /** A window is open over the transcript: 1 / 2 belong to it. */
  blocked: boolean
}

export function useCharacterPicks({ jobId, segments, speakers, goTo, blocked }: Options) {
  const qc = useQueryClient()
  const { mutateAsync: pickCharacter } = usePickCharacter()
  // Read synchronously by a second click or key before React re-renders
  const pendingRef = useRef<Record<string, PendingPick>>({})
  const [pending, setPending] = useState<Record<string, PendingPick>>({})
  const setPendingFor = useCallback((id: string, p: PendingPick | null) => {
    const next = { ...pendingRef.current }
    if (p) next[id] = p
    else delete next[id]
    pendingRef.current = next
    setPending(next)
  }, [])

  const live = useRef({ jobId, segments, speakers, goTo, blocked })
  useEffect(() => { live.current = { jobId, segments, speakers, goTo, blocked } })
  // The episode is still on screen: a pick that answers after a switch of
  // episode isn't put in the new episode's undo history (it was cleared)
  const onScreen = useRef(true)
  useEffect(() => {
    onScreen.current = true
    return () => { onScreen.current = false }
  }, [])

  const pick = useCallback(async (seg: Segment, speaker: Speaker, from: PickFrom) => {
    if (pendingRef.current[seg.id]) return
    setPendingFor(seg.id, { speakerId: speaker.id, from })
    const { jobId } = live.current
    if (from !== 'select') {
      // The newest lines (the cache), passing over this one and any other pick on its way
      const lines = qc.getQueryData<Segment[]>(['segments', jobId]) ?? live.current.segments
      const next = nextToCheck(lines, seg.id, { skip: new Set(Object.keys(pendingRef.current)) })
      if (next) live.current.goTo(next)
      else useEditorStore.getState().setCheckingLineId(null)
    }
    try {
      const picked = await pickCharacter({ segmentId: seg.id, speakerId: speaker.id, jobId })
      if (onScreen.current) recordCharacterPick(qc, jobId, seg.id, pickUndoPatches(seg, picked))
      if (picked.retranslated) toast.success(`Khmer rewritten for ${characterName(speaker)}`)
    } catch (err) {
      toast.error(`Couldn't pick ${characterName(speaker)}: ${(err as Error).message}`)
      console.error(err)
      // The server saves the pick before it rewrites the Khmer: after a timeout or a
      // dropped connection the line may well be picked — show what it really holds
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      qc.invalidateQueries({ queryKey: ['speakers'] })
    } finally {
      setPendingFor(seg.id, null)
    }
  }, [qc, pickCharacter, setPendingFor])

  /** "Unassigned": no character. Undo gives the speaker back without confirming the line. */
  const unassign = useCallback(async (seg: Segment) => {
    if (pendingRef.current[seg.id]) return
    const { jobId } = live.current
    const patches = unassignPatches(seg)
    try {
      putSegments(qc, jobId, [await segmentsApi.update(seg.id, patches.after)])
      qc.invalidateQueries({ queryKey: ['segments', jobId] })
      qc.invalidateQueries({ queryKey: ['speakers'] })
      recordCharacterPick(qc, jobId, seg.id, patches, 'Unassign speaker')
    } catch (err) {
      toast.error('Failed to assign speaker')
      console.error(err)
    }
  }, [qc])

  // 1 / 2 pick a guess for the line being checked — never while typing, with
  // Settings or a window or menu open, or while that line's pick is on its way.
  // (No other editor shortcut uses the digit keys.)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '1' && e.key !== '2') return
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || isTypingTarget(e.target)) return
      const st = useEditorStore.getState()
      const id = st.checkingLineId
      if (!id || st.settingsOpen || st.inspectorMode !== 'global_synthesis') return
      // An open menu (ContextMenu's list) takes the keys
      if (live.current.blocked || document.querySelector('[role="menu"][data-context-menu]')) return
      if (pendingRef.current[id]) return
      const seg = live.current.segments.find((s) => s.id === id)
      if (!seg || !isToCheck(seg)) return
      const chip = guessChips(seg, live.current.speakers).find((c) => c.key === Number(e.key))
      if (!chip) return
      e.preventDefault()
      pick(seg, chip.speaker, 'chip')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pick])

  return { pick, unassign, pending }
}
