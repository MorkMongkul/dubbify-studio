// src/lib/chatMessages.ts
// The Assistant chat while a message is on its way: what you sent shows at
// once as a stand-in, and the turn the server saved (your message and the
// reply) takes its place. No runtime imports, so `npm test` runs it directly.
import type { ChatMessage } from '@/types'

/** What you just sent, shown until the server's copy comes back with the reply. */
export function standInMessage(projectId: string, text: string, now: Date): ChatMessage {
  return { id: `sending-${now.getTime()}`, project_id: projectId, role: 'user', text, created_at: now.toISOString() }
}

/**
 * The list with the turn in place of the stand-in, each message once: the list
 * may have been fetched again meanwhile, with your message already saved.
 */
export function withTurn(list: ChatMessage[], standInId: string, turn: ChatMessage[]): ChatMessage[] {
  const kept = list.filter((m) => m.id !== standInId)
  const known = new Set(kept.map((m) => m.id))
  return [...kept, ...turn.filter((m) => !known.has(m.id))]
}
