// src/hooks/usePreviewAudio.ts
// The preview's sound, played the way an editor plays it (lib/audioPlan): every
// voice and both stems — the music & effects and the original voices — are
// decoded into memory and started on the one AudioContext clock at exactly the
// right offset, so nothing is ever seeked while it plays. Media elements kept
// in step by seeking cut the sound in Safari and the desktop app: WebKit stalls
// a playing element ~0.6 s on every seek, the stall puts it behind, and it is
// seeked again (measured 2026-10-24). The picture is never touched while it
// plays either — WebKit stops a video outright while its speed changes: when
// picture and sound drift apart further than the eye forgives (a seek, a
// stall), the sound starts again from where the picture is (lib/playbackSync).

import { useEffect, useRef, useState, type RefObject } from 'react'
import type { Segment } from '@/types'
import {
  anchorAt, fadePoints, heardAt, LEAD, learnedDelay, loadOrder, mediaSoundStart, previewVoiceUrl, shiftAnchor, soundKey, startTimes,
  stillAnchor, toCtx, toMedia, upcoming, voiceStart, type Anchor,
} from '@/lib/audioPlan'
import { fadeGainAt, isVoiced, voiceUrl } from '@/lib/clipRules'
import { SETTLE_MS, syncAction } from '@/lib/playbackSync'
import { useEditorStore } from '@/store/editorStore'

/** Every voice of the episode loads in the background, this many at a time
 * (a voice faster than 1× is made by the server on its first load)… */
const LOADING_AT_ONCE = 4
/** …and the ones this far ahead of the playhead (s) at once, whatever the queue. */
const LOAD_AHEAD = 8
/** Voices are scheduled this far ahead. */
const START_AHEAD = 1
/** Pressing play waits, at most this long (s), for the sounds of the first… */
const READY_WAIT = 1.5
/** …this many seconds (right after an episode opens they may still be loading). */
const READY_SPAN = 1.5
/** A voice or stem that changes while it plays crosses over to its new sound in this long (s). */
const SWAP_SECS = 0.012
/** Decoded voices kept (a few hundred KB each); the oldest used go first. */
const MAX_VOICES = 200
/** A failed load is tried again after this long (ms). */
const RETRY_MS = 5000
/** Playing waits at most this long (ms) for a picture that is still loading or seeking. */
const PLAYABLE_WAIT_MS = 3000
/** How long the picture takes to start moving after play() (s), until this Mac has shown us. */
const FIRST_VIDEO_DELAY = 0.12

export type Stem = 'music' | 'voices'

let context: AudioContext | null = null
function audioContext(): AudioContext | null {
  if (context) return context
  const w = window as Window & typeof globalThis & { webkitAudioContext?: typeof AudioContext }
  const Ctx = w.AudioContext || w.webkitAudioContext
  if (!Ctx) return null
  try { context = new Ctx() } catch { return null }
  return context
}

/** How long after it is sent a sound is heard (Bluetooth earbuds: ~0.4 s). */
function outputDelay(ctx: AudioContext): number {
  return (ctx.baseLatency || 0) + ((ctx as AudioContext & { outputLatency?: number }).outputLatency || 0)
}

// The picture's start delay is learned on each start and kept on this Mac, per
// output delay (Bluetooth earbuds and the Mac's speakers differ)
const delayKey = (latency: number) => `movelo.pictureStart.${Math.round(latency * 10)}`
function videoDelayFor(latency: number): number {
  try {
    const v = parseFloat(localStorage.getItem(delayKey(latency)) ?? '')
    return Number.isFinite(v) ? v : FIRST_VIDEO_DELAY
  } catch {
    return FIRST_VIDEO_DELAY
  }
}
function rememberVideoDelay(latency: number, delay: number): void {
  try { localStorage.setItem(delayKey(latency), delay.toFixed(3)) } catch { /* this session only */ }
}

// Videos the engine paused itself, to start them again in step with the sound
const held = new WeakSet<HTMLMediaElement>()

/** The engine paused this video to start it again with the sound: not the editor's pause (VideoPlayer). */
export function heldByEngine(video: HTMLMediaElement): boolean {
  return held.has(video)
}

function holdPicture(video: HTMLMediaElement): void {
  held.add(video)
  if (!video.paused) video.pause()
}

function releasePicture(video: HTMLMediaElement): void {
  // Held until this play's own event: the pause's event, if still queued, comes first
  const done = () => held.delete(video)
  video.addEventListener('play', done, { once: true })
  video.play().catch(done)
}

/** Until the picture can play — not loading, not seeking — or `ms` have gone by. */
function playable(video: HTMLVideoElement, ms: number): Promise<void> {
  if (video.readyState >= 3 && !video.seeking) return Promise.resolve()
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer)
      video.removeEventListener('canplay', check)
      video.removeEventListener('seeked', check)
      resolve()
    }
    const check = () => { if (video.readyState >= 3 && !video.seeking) done() }
    const timer = setTimeout(done, ms)
    video.addEventListener('canplay', check)
    video.addEventListener('seeked', check)
  })
}

async function fetchDecode(ctx: AudioContext, url: string): Promise<AudioBuffer | null> {
  try {
    const res = await fetch(url)
    if (!res.ok) return null
    return await ctx.decodeAudioData(await res.arrayBuffer())
  } catch {
    return null
  }
}

// Voices by URL, oldest used first
const voiceLoads = new Map<string, Promise<AudioBuffer | null>>()
const voiceBuffers = new Map<string, AudioBuffer>()
function loadVoice(ctx: AudioContext, url: string): Promise<AudioBuffer | null> {
  const pending = voiceLoads.get(url)
  if (pending) {
    voiceLoads.delete(url)
    voiceLoads.set(url, pending)
    return pending
  }
  const load = fetchDecode(ctx, url).then((buffer) => {
    if (buffer) voiceBuffers.set(url, buffer)
    else setTimeout(() => { if (voiceLoads.get(url) === load) voiceLoads.delete(url) }, RETRY_MS)
    return buffer
  })
  voiceLoads.set(url, load)
  while (voiceLoads.size > MAX_VOICES) {
    const oldest = voiceLoads.keys().next().value as string
    voiceLoads.delete(oldest)
    voiceBuffers.delete(oldest)
  }
  return load
}

// The episode's voices waiting to load, in order; a new list replaces it
let voiceQueue: string[] = []
let loadingNow = 0
function pumpVoices(ctx: AudioContext): void {
  while (loadingNow < LOADING_AT_ONCE && voiceQueue.length) {
    const url = voiceQueue.shift()!
    if (voiceLoads.has(url)) continue
    loadingNow++
    loadVoice(ctx, url).finally(() => { loadingNow--; pumpVoices(ctx) })
  }
}
function queueVoices(ctx: AudioContext, urls: string[]): void {
  voiceQueue = urls.filter((u) => !voiceLoads.has(u))
  pumpVoices(ctx)
}

// The open episode's stems (≈50 MB each in memory): another episode's are let go
const stemLoads = new Map<string, Promise<AudioBuffer | null>>()
const stemBuffers = new Map<string, AudioBuffer>()
function loadStems(ctx: AudioContext, files: string[][]): void {
  const keys = files.map((urls) => urls.join(' '))
  for (const key of [...stemLoads.keys()]) {
    if (!keys.includes(key)) { stemLoads.delete(key); stemBuffers.delete(key) }
  }
  files.forEach((urls, i) => {
    const key = keys[i]
    if (stemLoads.has(key)) return
    // The compressed copy first, the WAV for episodes separated before it existed
    const load = (async () => {
      for (const url of urls) {
        const buffer = await fetchDecode(ctx, url)
        if (buffer) return buffer
      }
      return null
    })().then((buffer) => {
      if (buffer && stemLoads.get(key) === load) stemBuffers.set(key, buffer)
      if (!buffer) setTimeout(() => { if (stemLoads.get(key) === load) stemLoads.delete(key) }, RETRY_MS)
      return buffer
    })
    stemLoads.set(key, load)
  })
}

interface Sound { src: AudioBufferSourceNode; level: GainNode; nodes: AudioNode[]; lastLevel: number }
interface VoiceSound extends Sound { key: string }
interface StemSound extends Sound { buffer: AudioBuffer }

function stopSound(s: Sound): void {
  try { s.src.stop() } catch { /* never started */ }
  for (const n of s.nodes) n.disconnect()
}

/** Fade a sound out from `when` over SWAP_SECS and let it go: its new sound fades in over it. */
function fadeOutSound(s: Sound, when: number): void {
  s.level.gain.cancelScheduledValues(when)
  s.level.gain.setValueAtTime(s.lastLevel, when)
  s.level.gain.linearRampToValueAtTime(0, when + SWAP_SECS)
  s.src.onended = () => { for (const n of s.nodes) n.disconnect() }
  try { s.src.stop(when + SWAP_SECS + 0.005) } catch { stopSound(s) }
}

/** Start a sound's level at `when`: at once, or rising over SWAP_SECS when it takes over from its old sound. */
function startLevel(level: GainNode, value: number, when: number, crossing: boolean): void {
  if (!crossing) { level.gain.value = value; return }
  level.gain.setValueAtTime(0, when)
  level.gain.linearRampToValueAtTime(value, when + SWAP_SECS)
}

/** Ease a level to its new value (a jump clicks); only when it changed. */
function setLevel(ctx: AudioContext, s: Sound, level: number): void {
  if (Math.abs(s.lastLevel - level) < 1e-4) return
  s.level.gain.setTargetAtTime(level, ctx.currentTime, 0.01)
  s.lastLevel = level
}

/** A volume line being dragged (one clip or a selection) plays at its new level before it is saved. */
function liveFactor(seg: Segment): number {
  const target = useEditorStore.getState().liveClipVolume?.[seg.id]
  return target == null ? 1 : 10 ** ((target - (seg.volume_db ?? 0)) / 20)
}

export interface PreviewAudioInput {
  videoRef: RefObject<HTMLVideoElement | null>
  /** The video's source: a new one is a new element to listen to. */
  videoUrl?: string
  isPlaying: boolean
  playbackRate: number
  volume: number
  /** The voice clips that are heard (muted tracks already left out). */
  segments: Segment[]
  /** Speakers muted in the editor. */
  muted: Record<string, boolean>
  /** Each stem's files, best first; null before the episode is separated. */
  stems: Record<Stem, string[]> | null
  /** A stem's level at a media time: its pieces, the dip under the voices, its switch, the volume. */
  stemLevel: RefObject<(stem: Stem, t: number) => number>
  /** The media clips' own sound (muted media tracks already left out). */
  mediaSounds?: MediaSound[]
}

/** A media clip's own sound (0.4.0): its file from `source_in`, for the clip's length, at its volume. */
export interface MediaSound {
  id: string
  url: string
  start_time: number
  end_time: number
  source_in: number
  volume_db: number
}

/** Everything that changes what a media clip plays (not its volume): a change starts it again. */
const mediaKey = (m: MediaSound) => [m.url, m.start_time, m.end_time, m.source_in].join('|')

class PreviewSound {
  input: PreviewAudioInput | null = null
  private anchor: Anchor | null = null
  private anchoredAt = 0
  private lastVideoTime: number | null = null
  /** The picture is waiting for the first sound to be heard. */
  private holding: ReturnType<typeof setTimeout> | null = null
  /** A start is waiting for its sounds or its picture to be ready. */
  private starting = false
  /** The first look after a start teaches the picture's start delay. */
  private learning = false
  /** Every sound starting next takes over from one fading out (a correction). */
  private crossAll = false
  /** The last frame the picture showed: its media time and when it was shown (performance.now). */
  frame: { media: number; at: number } | null = null
  private starts = 0
  private voices = new Map<string, VoiceSound>()
  private stems = new Map<Stem, StemSound>()
  private media = new Map<string, VoiceSound>()

  /** Silence everything; the next frame of playback starts again from where the video is. */
  stop(): void {
    this.starts++
    if (this.holding) { clearTimeout(this.holding); this.holding = null }
    for (const s of this.voices.values()) stopSound(s)
    for (const s of this.stems.values()) stopSound(s)
    for (const s of this.media.values()) stopSound(s)
    this.voices.clear()
    this.stems.clear()
    this.media.clear()
    this.anchor = null
    this.lastVideoTime = null
    this.starting = false
    this.learning = false
    this.crossAll = false
    this.frame = null
  }

  private heard(): Segment[] {
    const { segments, muted } = this.input!
    return segments.filter((s) => isVoiced(s) && !muted[s.speaker_id ?? '__none__'])
  }

  /** Load every voice of the episode, from the playhead on, so playing starts with them. */
  preload(): void {
    const ctx = audioContext()
    const video = this.input?.videoRef.current
    if (!ctx || !video) return
    const near = (this.input?.mediaSounds ?? []).filter((m) => m.end_time > video.currentTime).map((m) => m.url)
    queueVoices(ctx, [...near, ...loadOrder(this.heard(), video.currentTime).map(previewVoiceUrl)])
  }

  /** The sounds of the first READY_SPAN s from `t`, loaded — or READY_WAIT s gone by. */
  private async ready(ctx: AudioContext, t: number): Promise<void> {
    const loads: Promise<unknown>[] = upcoming(this.heard(), t, READY_SPAN).map((seg) => loadVoice(ctx, previewVoiceUrl(seg)))
    for (const m of this.input?.mediaSounds ?? []) {
      if (m.end_time > t && m.start_time < t + READY_SPAN) loads.push(loadVoice(ctx, m.url))
    }
    const stems = this.input?.stems
    if (stems) for (const urls of [stems.music, stems.voices]) loads.push(stemLoads.get(urls.join(' ')) ?? Promise.resolve())
    await Promise.race([Promise.all(loads), new Promise((r) => setTimeout(r, READY_WAIT * 1000))])
  }

  /**
   * Play from the still picture: once its sounds and the picture are ready, the
   * sound starts at once from the playhead, and the picture is released so that
   * its first frame shows as that sound is heard. Every start goes this way — a
   * press of play, a seek or a stall while playing, a new speed (restart).
   */
  async start(): Promise<void> {
    const ctx = audioContext()
    const video = this.input?.videoRef.current
    if (!video) return
    this.stop()
    const start = this.starts
    this.starting = true
    if (ctx && ctx.state !== 'running') {
      await Promise.race([ctx.resume().catch(() => {}), new Promise((r) => setTimeout(r, 300))])
    }
    if (ctx) await this.ready(ctx, video.currentTime)
    await playable(video, PLAYABLE_WAIT_MS)
    if (start !== this.starts || !this.input?.isPlaying) return        // stopped meanwhile
    this.starting = false
    if (!ctx || ctx.state !== 'running') {
      // No sound clock: the picture plays alone, and the sound joins once it runs (tick)
      releasePicture(video)
      return
    }
    const latency = outputDelay(ctx)
    const { soundLead, hold } = startTimes(latency, videoDelayFor(latency))
    this.anchor = stillAnchor(ctx.currentTime, video.currentTime, this.input.playbackRate || 1, soundLead)
    this.anchoredAt = performance.now() + hold * 1000
    this.learning = true
    this.holding = setTimeout(() => {
      this.holding = null
      releasePicture(video)
    }, hold * 1000)
    this.tick()
  }

  /** Playing, but the picture has to start over (a seek, a stall, a new speed): hold it, start again in step. */
  restart(): void {
    const video = this.input?.videoRef.current
    if (!video || !this.input?.isPlaying) return
    holdPicture(video)
    void this.start()
  }

  /**
   * Picture − sound heard (s), on the last frame shown; null until one has been
   * shown since the start. Only a browser that can't tell its frames is judged
   * on the video's position: right after a start, Safari's ran ~0.3 s ahead of
   * the frames on screen (measured 2026-10-24).
   */
  private drift(ctx: AudioContext, video: HTMLVideoElement, a: Anchor, latency: number): number | null {
    const f = this.frame
    const nowMs = performance.now()
    if (f && nowMs - f.at < 200) return f.media - toMedia(a, ctx.currentTime + (f.at - nowMs) / 1000 - latency)
    if (typeof (video as { requestVideoFrameCallback?: unknown }).requestVideoFrameCallback === 'function') return null
    return video.currentTime - heardAt(a, ctx.currentTime, latency)
  }

  /** Every sound fades out where it is; this pass starts them again from the anchor, fading in. */
  private crossOver(ctx: AudioContext): void {
    const when = ctx.currentTime + LEAD
    for (const s of this.voices.values()) fadeOutSound(s, when)
    for (const s of this.stems.values()) fadeOutSound(s, when)
    for (const s of this.media.values()) fadeOutSound(s, when)
    this.voices.clear()
    this.stems.clear()
    this.media.clear()
    this.crossAll = true
  }

  /** One frame of playback. */
  tick(): void {
    const ctx = audioContext()
    const input = this.input
    const video = input?.videoRef.current
    if (!ctx || !input || !video || this.starting) return
    if ((video.paused && !this.holding && !held.has(video)) || video.ended) { if (this.anchor) this.stop(); return }
    if (ctx.state !== 'running') { ctx.resume().catch(() => {}); return }
    const rate = input.playbackRate || 1
    const latency = outputDelay(ctx)
    // A new speed (J / K / L): start again in step
    if (this.anchor && this.anchor.rate !== rate) { this.restart(); return }

    if (!this.anchor) {
      // Only without a sound clock at the start: join the picture once it is really moving
      if (video.seeking || video.readyState < 3) { this.lastVideoTime = null; return }
      const t = video.currentTime
      if (this.lastVideoTime === null || t <= this.lastVideoTime) { this.lastVideoTime = t; return }
      this.anchor = anchorAt(ctx.currentTime, t, rate, latency)
      this.anchoredAt = performance.now()
    }
    let a = this.anchor

    // The sound follows the picture, never the other way: measured on the frames
    // it shows; too far apart, the sound crosses over to where the picture is
    const drift = this.holding ? null : this.drift(ctx, video, a, latency)
    if (drift != null) {
      const settled = performance.now() - this.anchoredAt
      if (this.learning && settled >= SETTLE_MS) {
        // The first look after a start: how long this Mac's picture really took (a stall isn't a start)
        if (Math.abs(drift) < 0.5) rememberVideoDelay(latency, learnedDelay(videoDelayFor(latency), drift))
        this.learning = false
      }
      if (syncAction(drift, settled) === 'resync') {
        a = this.anchor = shiftAnchor(a, drift)
        this.anchoredAt = performance.now()
        this.crossOver(ctx)
      }
    }

    const now = toMedia(a, ctx.currentTime)
    const startAt = (media: number) => Math.max(toCtx(a, media), ctx.currentTime + LEAD)

    // The stems: one sound each for the whole episode
    if (input.stems) {
      for (const stem of ['music', 'voices'] as const) {
        const buffer = stemBuffers.get(input.stems[stem].join(' '))
        let s = this.stems.get(stem)
        // Made again (a music level change): the old sound plays until the new one is loaded
        let crossing = this.crossAll
        if (s && buffer && s.buffer !== buffer) {
          fadeOutSound(s, startAt(now))
          this.stems.delete(stem)
          s = undefined
          crossing = true
        }
        if (!s && buffer && now < buffer.duration) {
          const when = startAt(now)
          const src = ctx.createBufferSource()
          src.buffer = buffer
          src.playbackRate.value = rate
          const level = ctx.createGain()
          startLevel(level, input.stemLevel.current(stem, toMedia(a, when)), when, crossing)
          src.connect(level).connect(ctx.destination)
          src.start(when, toMedia(a, when))
          s = { src, level, nodes: [src, level], lastLevel: input.stemLevel.current(stem, toMedia(a, when)), buffer }
          this.stems.set(stem, s)
        }
        if (s) setLevel(ctx, s, input.stemLevel.current(stem, now))
      }
    }

    // The voices: each clip from its start (or mid-way, when it comes in late) to its end
    const heard = this.heard()
    for (const seg of upcoming(heard, now, LOAD_AHEAD)) loadVoice(ctx, previewVoiceUrl(seg))
    const wanted = new Map(upcoming(heard, now, START_AHEAD).map((s) => [s.id, s]))
    const crossing = new Set<string>()
    for (const [id, s] of this.voices) {
      const seg = wanted.get(id)
      if (!seg || seg.end_time <= now) { stopSound(s); this.voices.delete(id); continue }
      if (s.key === soundKey(seg)) continue
      // Changed while it plays (a speed, a filter, a trim): it plays on until the
      // new sound is loaded, then the two cross over — no gap
      if (!voiceBuffers.has(previewVoiceUrl(seg))) { loadVoice(ctx, previewVoiceUrl(seg)); continue }
      fadeOutSound(s, startAt(now))
      this.voices.delete(id)
      crossing.add(id)
    }
    for (const seg of wanted.values()) {
      const playing = this.voices.get(seg.id)
      const level = input.volume * liveFactor(seg)
      if (playing) { setLevel(ctx, playing, level); continue }
      const buffer = voiceBuffers.get(previewVoiceUrl(seg))
      if (!buffer) continue        // still loading: it comes in, mid-way, once it's here
      const when = startAt(seg.start_time)
      const plan = voiceStart(seg, toMedia(a, when))
      if (!plan) continue
      const src = ctx.createBufferSource()
      src.buffer = buffer
      src.playbackRate.value = rate
      // The fades, on the clock: straight lines between the envelope's corners
      const fade = ctx.createGain()
      const points = fadePoints(seg, plan.at)
      fade.gain.setValueAtTime(points[0][1], ctx.currentTime)
      fade.gain.setValueAtTime(points[0][1], when)
      for (const [m, g] of points.slice(1)) fade.gain.linearRampToValueAtTime(g, toCtx(a, m))
      const gain = ctx.createGain()
      startLevel(gain, level, when, this.crossAll || crossing.has(seg.id))
      src.connect(fade).connect(gain).connect(ctx.destination)
      src.start(when, plan.offset)
      src.stop(toCtx(a, seg.end_time))
      this.voices.set(seg.id, { src, level: gain, nodes: [src, fade, gain], lastLevel: level, key: soundKey(seg) })
    }

    // The media clips' own sound: like a voice, from where the clip begins in its file, at its
    // volume (the file is the media's own sound, so the clip's volume is applied here, as the export does)
    const sounds = input.mediaSounds ?? []
    for (const m of sounds) if (m.end_time > now && m.start_time < now + LOAD_AHEAD) loadVoice(ctx, m.url)
    const wantedMedia = new Map(sounds.filter((m) => m.end_time > now && m.start_time < now + START_AHEAD).map((m) => [m.id, m]))
    const crossingMedia = new Set<string>()
    for (const [id, s] of this.media) {
      const m = wantedMedia.get(id)
      if (m && s.key === mediaKey(m)) continue
      // Moved or trimmed while it plays: the new sound takes over from the old one
      if (m) { fadeOutSound(s, startAt(now)); crossingMedia.add(id) } else stopSound(s)
      this.media.delete(id)
    }
    for (const m of wantedMedia.values()) {
      const level = input.volume * 10 ** (m.volume_db / 20)
      const playing = this.media.get(m.id)
      if (playing) { setLevel(ctx, playing, level); continue }
      const buffer = voiceBuffers.get(m.url)
      if (!buffer) continue        // still loading: it comes in, mid-way, once it's here
      const when = startAt(m.start_time)
      const plan = mediaSoundStart(m, toMedia(a, when))
      if (!plan || plan.offset >= buffer.duration) continue
      const src = ctx.createBufferSource()
      src.buffer = buffer
      src.playbackRate.value = rate
      const gain = ctx.createGain()
      startLevel(gain, level, when, this.crossAll || crossingMedia.has(m.id))
      src.connect(gain).connect(ctx.destination)
      src.start(when, plan.offset)
      src.stop(toCtx(a, m.end_time))
      this.media.set(m.id, { src, level: gain, nodes: [src, gain], lastLevel: level, key: mediaKey(m) })
    }
    this.crossAll = false
  }
}

/** Play the preview's sound with the video: the voices, the music & effects and the original voices. */
export function usePreviewAudio(input: PreviewAudioInput): void {
  const [sound] = useState(() => new PreviewSound())
  useEffect(() => { sound.input = input })

  // The stems load as soon as the episode opens (≈1 s), so playing starts with them
  const files = input.stems
  const stemKey = files ? `${files.music.join(' ')}\n${files.voices.join(' ')}` : ''
  const filesRef = useRef(files)
  useEffect(() => { filesRef.current = files })
  useEffect(() => {
    const ctx = audioContext()
    if (ctx && filesRef.current) loadStems(ctx, [filesRef.current.music, filesRef.current.voices])
  }, [stemKey])

  // Play and pause the video with the sound; every frame while playing; when
  // stopped, the voices around the playhead get loaded
  const { isPlaying, videoRef, segments, videoUrl } = input
  useEffect(() => {
    if (!isPlaying) {
      sound.stop()
      const video = videoRef.current
      if (video) { held.delete(video); video.pause() }
      sound.preload()
      return
    }
    sound.start()
    let raf = requestAnimationFrame(function frame() {
      sound.tick()
      raf = requestAnimationFrame(frame)
    })
    return () => { cancelAnimationFrame(raf); sound.stop() }
  }, [isPlaying, videoUrl, videoRef, sound])
  useEffect(() => { if (!isPlaying) sound.preload() }, [segments, isPlaying, sound])

  // The frames the picture shows (media time, when shown): the sound is kept with them
  useEffect(() => {
    const video = videoRef.current
    if (!isPlaying || !video || !('requestVideoFrameCallback' in video)) return
    let handle = 0
    const onFrame: VideoFrameRequestCallback = (_now, meta) => {
      sound.frame = { media: meta.mediaTime, at: meta.expectedDisplayTime }
      handle = video.requestVideoFrameCallback(onFrame)
    }
    handle = video.requestVideoFrameCallback(onFrame)
    return () => video.cancelVideoFrameCallback(handle)
  }, [isPlaying, videoUrl, videoRef, sound])

  // Browsers start an AudioContext only from a key press or a click: wake it in one
  useEffect(() => {
    const wake = () => { if (context?.state === 'suspended') context.resume().catch(() => {}) }
    window.addEventListener('keydown', wake, true)
    window.addEventListener('pointerdown', wake, true)
    return () => {
      window.removeEventListener('keydown', wake, true)
      window.removeEventListener('pointerdown', wake, true)
    }
  }, [])

  // A seek or a stall while playing: silence at once; once the picture is there
  // it starts again in step (held, then released after the sound)
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const silence = () => sound.stop()
    const again = () => { if (sound.input?.isPlaying) sound.restart(); else sound.preload() }
    video.addEventListener('seeking', silence)
    video.addEventListener('seeked', again)
    video.addEventListener('waiting', again)
    return () => {
      video.removeEventListener('seeking', silence)
      video.removeEventListener('seeked', again)
      video.removeEventListener('waiting', again)
    }
  }, [videoRef, videoUrl, sound])

  useEffect(() => () => sound.stop(), [sound])
}

// ── One clip on its own (double-click on the timeline) ────────────────────────

let preview: { audio: HTMLAudioElement; raf: number } | null = null

/** Stop a clip started with previewClip. */
export function stopPreview(): void {
  if (!preview) return
  cancelAnimationFrame(preview.raf)
  preview.audio.pause()
  preview = null
}

/** Play one clip's window once — the part of its voice the clip plays, at its rate, with its fades. */
export function previewClip(seg: Segment, volume = 1): void {
  stopPreview()
  if (!isVoiced(seg)) return
  const rate = seg.fit_rate || 1
  const from = seg.voice_offset ?? 0
  const until = from + (seg.end_time - seg.start_time) * rate
  const audio = new Audio(voiceUrl(seg))
  audio.preload = 'auto'
  // A rate other than 1 is a time-stretch, like the export's atempo
  ;(audio as HTMLAudioElement & { preservesPitch?: boolean }).preservesPitch = true
  audio.volume = Math.max(0, Math.min(1, volume * fadeGainAt(seg, seg.start_time)))
  audio.playbackRate = Math.max(0.0625, Math.min(16, rate))
  const current = { audio, raf: 0 }
  preview = current
  const watch = () => {
    if (preview !== current) return
    if (audio.currentTime >= until || audio.ended) { stopPreview(); return }
    // Where the clip is on the timeline now, for its fades
    audio.volume = Math.max(0, Math.min(1, volume * fadeGainAt(seg, seg.start_time + (audio.currentTime - from) / rate)))
    current.raf = requestAnimationFrame(watch)
  }
  audio.currentTime = from
  audio.play().then(() => { current.raf = requestAnimationFrame(watch) }).catch(() => {})
}
