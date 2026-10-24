// src/components/timeline/DragTip.tsx
// The value being dragged (a volume line's dB), shown just above the pointer
// like CapCut — in a portal, so no clip or row can clip it.
import { createPortal } from 'react-dom'

export function DragTip({ x, y, text }: { x: number; y: number; text: string }) {
  return createPortal(
    <div
      className="fixed z-[200] -translate-x-1/2 -translate-y-full rounded-md bg-black/90 px-2 py-1 text-[12px] font-semibold text-white tabular-nums shadow-lg pointer-events-none"
      style={{ left: x, top: y - 12 }}
    >
      {text}
    </div>,
    document.body,
  )
}
