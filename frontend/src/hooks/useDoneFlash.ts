// src/hooks/useDoneFlash.ts
import { useEffect, useState } from 'react'

/**
 * True for `ms` after a wait (`running`) ends well (`succeeded`), so its
 * progress ring can say "Done" before the result takes its place. Not after a
 * failure: the error shows instead.
 */
export function useDoneFlash(running: boolean, succeeded: boolean, ms = 1400): boolean {
  const [wasRunning, setWasRunning] = useState(running)
  const [flashing, setFlashing] = useState(false)
  if (wasRunning !== running) {
    // Adjusting state while rendering when `running` changes (React's pattern
    // for state that follows a prop), so the first finished frame already says Done
    setWasRunning(running)
    setFlashing(wasRunning && !running && succeeded)
  }
  useEffect(() => {
    if (!flashing) return
    const timer = setTimeout(() => setFlashing(false), ms)
    return () => clearTimeout(timer)
  }, [flashing, ms])
  return flashing
}
