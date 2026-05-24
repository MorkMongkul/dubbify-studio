// src/lib/utils.ts
// Utility helpers

import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { SPEAKER_COLORS } from '@/types'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Format seconds to MM:SS or HH:MM:SS */
export function formatTime(seconds: number, forceHours = false): string {
  if (!isFinite(seconds) || seconds < 0) return '0:00'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const pad = (n: number) => String(n).padStart(2, '0')
  if (h > 0 || forceHours) return `${h}:${pad(m)}:${pad(s)}`
  return `${m}:${pad(s)}`
}

/** Format seconds to precise timestamp: MM:SS.mmm */
export function formatTimePrecise(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '0:00.000'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.round((seconds % 1) * 1000)
  return `${m}:${String(s).padStart(2, '0')}.${String(ms).padStart(3, '0')}`
}

/** Get speaker color by index (cycles through palette) */
export function getSpeakerColor(index: number): string {
  return SPEAKER_COLORS[index % SPEAKER_COLORS.length]
}

/** Compute hex → rgba with alpha */
export function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

/** Clamp a value between min and max */
export function clamp(val: number, min: number, max: number): number {
  return Math.min(Math.max(val, min), max)
}

/** Convert time (seconds) to timeline pixel position */
export function timeToPixels(time: number, zoom: number, pixelsPerSecond = 100): number {
  return time * pixelsPerSecond * zoom
}

/** Convert pixel position to time (seconds) */
export function pixelsToTime(pixels: number, zoom: number, pixelsPerSecond = 100): number {
  return pixels / (pixelsPerSecond * zoom)
}

/** Truncate text to max characters with ellipsis */
export function truncate(str: string, maxLen = 60): string {
  if (str.length <= maxLen) return str
  return str.slice(0, maxLen - 1) + '…'
}

/** Debounce a function */
export function debounce<T extends (...args: Parameters<T>) => void>(
  fn: T,
  delay: number
): (...args: Parameters<T>) => void {
  let timer: ReturnType<typeof setTimeout>
  return (...args: Parameters<T>) => {
    clearTimeout(timer)
    timer = setTimeout(() => fn(...args), delay)
  }
}

/** Map job status to pipeline step index */
export function getJobStepIndex(status: string): number {
  const steps = ['uploading', 'extracting', 'diarizing', 'transcribing', 'translating', 'ready', 'synthesizing', 'mixing', 'done']
  return steps.indexOf(status)
}

/** Check if job is still processing */
export function isJobRunning(status: string): boolean {
  const running = ['pending', 'uploading', 'extracting', 'diarizing', 'transcribing', 'translating', 'synthesizing', 'mixing']
  return running.includes(status)
}

/** Get human-readable language name */
export function getLanguageName(code: string | null | undefined): string {
  if (!code) return '—'
  const map: Record<string, string> = {
    zh: 'Chinese', kh: 'Khmer',
    en: 'English', th: 'Thai', ja: 'Japanese', ko: 'Korean',
    es: 'Spanish', fr: 'French', de: 'German', it: 'Italian', pt: 'Portuguese',
    ru: 'Russian', ar: 'Arabic', hi: 'Hindi', vi: 'Vietnamese', id: 'Indonesian',
  }
  return map[code] ?? code.toUpperCase()
}
