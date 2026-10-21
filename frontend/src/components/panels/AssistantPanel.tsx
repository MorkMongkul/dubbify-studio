// src/components/panels/AssistantPanel.tsx
// Assistant tab of the dock — a project-scoped chat that can search this
// project's segments across every episode and propose translation edits.
// Edit approval has two modes (mirrors an agentic coding tool's auto/ask
// toggle): "manual" stages every edit for approval below the chat before it
// touches a segment; "auto" applies immediately but still logs an
// approved-status row so there's an audit trail to review or revert from.
import { useEffect, useRef, useState } from 'react'
import { Bot, Send, Check, X, Search, ListTree, Pencil, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  useProject, useUpdateProject,
  useChatMessages, useSendChatMessage,
  usePendingEdits, useApproveEdit, useRejectEdit,
} from '@/hooks/useApi'
import type { ChatEditMode, ChatMessage } from '@/types'
import { PanelHeader, PanelEmptyState } from './PanelShell'
import { cn } from '@/lib/utils'

interface AssistantPanelProps {
  projectId?: string
}

const TOOL_ICONS: Record<string, typeof Search> = {
  search_segments: Search,
  list_episodes: ListTree,
  propose_edit: Pencil,
}

function ToolStep({ label, icon: Icon }: { label: string; icon: typeof Search }) {
  return (
    <div className="flex items-center gap-1.5 text-[9px] text-ink-600 pl-1 py-0.5">
      <Icon size={10} className="shrink-0" />
      <span className="truncate">{label}</span>
    </div>
  )
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[85%] rounded-lg px-2.5 py-1.5 text-[11px] leading-relaxed whitespace-pre-wrap break-words',
          isUser
            ? 'bg-brand-500/15 text-ink-100'
            : 'bg-ink-800/60 text-ink-200'
        )}
      >
        {message.text}
      </div>
    </div>
  )
}

export function AssistantPanel({ projectId }: AssistantPanelProps) {
  const { data: project } = useProject(projectId ?? null)
  const updateProject = useUpdateProject()
  const { data: messages = [], isLoading: loadingMessages } = useChatMessages(projectId ?? null)
  const sendMessage = useSendChatMessage()
  const { data: pendingEdits = [] } = usePendingEdits(projectId ?? null)
  const approveEdit = useApproveEdit()
  const rejectEdit = useRejectEdit()

  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages.length])

  if (!projectId) {
    return (
      <div className="h-full flex flex-col min-h-0">
        <PanelHeader icon={Bot} title="Assistant" />
        <PanelEmptyState
          icon={Bot}
          title="No project open"
          hint="Open a project to use the Assistant"
        />
      </div>
    )
  }

  const mode: ChatEditMode = project?.chat_edit_mode ?? 'manual'
  const setMode = (m: ChatEditMode) => {
    if (m === mode) return
    updateProject.mutate({ id: projectId, data: { chat_edit_mode: m } })
  }

  const handleSend = () => {
    const text = draft.trim()
    if (!text || sendMessage.isPending) return
    setDraft('')
    sendMessage.mutate(
      { projectId, text },
      { onError: (err: Error) => toast.error(err.message || 'Assistant request failed') }
    )
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader
        icon={Bot}
        title="Assistant"
        action={
          <div className="flex items-center rounded-md bg-ink-950 border border-ink-800/60 p-0.5 text-[9px]">
            {(['manual', 'auto'] as ChatEditMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={cn(
                  'px-1.5 py-0.5 rounded transition-colors capitalize',
                  mode === m ? 'bg-brand-500/20 text-brand-300' : 'text-ink-600 hover:text-ink-400'
                )}
                title={m === 'manual' ? 'Edits wait for your approval below' : 'Edits apply immediately (still logged for review)'}
              >
                {m}
              </button>
            ))}
          </div>
        }
      />

      {pendingEdits.length > 0 && (
        <div className="border-b border-ink-800/50 bg-ink-950/40 max-h-[40%] overflow-y-auto shrink-0">
          <div className="px-3 pt-2 pb-1 text-[9px] font-semibold text-ink-500 uppercase tracking-wide">
            Pending edits ({pendingEdits.length})
          </div>
          <div className="px-2 pb-2 space-y-1.5">
            {pendingEdits.map((edit) => (
              <div key={edit.id} className="p-2 rounded bg-ink-900/80 border border-ink-800/60 space-y-1">
                <p className="text-[10px] text-ink-500 line-through decoration-ink-700 truncate">{edit.old_khmer_text}</p>
                <p className="text-[11px] text-ink-200 truncate">{edit.new_khmer_text}</p>
                <p className="text-[9px] text-ink-600 leading-snug">{edit.reason}</p>
                <div className="flex gap-1 pt-0.5">
                  <button
                    onClick={() => approveEdit.mutate({ editId: edit.id, projectId })}
                    disabled={approveEdit.isPending}
                    className="flex items-center gap-0.5 text-[9px] font-semibold text-emerald-400 hover:text-emerald-300 px-1.5 py-0.5 rounded hover:bg-emerald-500/10 transition-colors disabled:opacity-40"
                  >
                    <Check size={10} /> Approve
                  </button>
                  <button
                    onClick={() => rejectEdit.mutate({ editId: edit.id, projectId })}
                    disabled={rejectEdit.isPending}
                    className="flex items-center gap-0.5 text-[9px] font-semibold text-red-400 hover:text-red-300 px-1.5 py-0.5 rounded hover:bg-red-500/10 transition-colors disabled:opacity-40"
                  >
                    <X size={10} /> Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-2 min-h-0">
        {loadingMessages ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 size={16} className="animate-spin text-ink-600" />
          </div>
        ) : messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-center">
            <Bot size={18} className="text-ink-700 mb-2" />
            <p className="text-[10px] text-ink-600">Ask about this project's translations</p>
            <p className="text-[9px] text-ink-700 mt-0.5 max-w-[200px] leading-normal">
              e.g. "How was 老公 translated in episode 2?" or "Make line 14 sound less formal"
            </p>
          </div>
        ) : (
          messages.map((m) => {
            if (m.function_call) {
              const Icon = TOOL_ICONS[m.function_call.name] ?? Search
              return <ToolStep key={m.id} icon={Icon} label={`Used ${m.function_call.name}`} />
            }
            if (m.function_response) return null   // the tool step above already represents this turn
            if (!m.text) return null
            return <ChatBubble key={m.id} message={m} />
          })
        )}
        {sendMessage.isPending && (
          <div className="flex items-center gap-1.5 text-[10px] text-ink-600 pl-1">
            <Loader2 size={11} className="animate-spin" /> Thinking…
          </div>
        )}
      </div>

      <div className="p-2 border-t border-ink-800/50 flex items-end gap-1.5 shrink-0">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          placeholder="Ask the Assistant…"
          rows={1}
          className="flex-1 resize-none bg-ink-950 text-[11px] text-ink-200 rounded-md px-2 py-1.5 border border-ink-800/60 focus:outline-none focus:border-brand-500/50 placeholder:text-ink-700 max-h-24"
        />
        <button
          onClick={handleSend}
          disabled={!draft.trim() || sendMessage.isPending}
          className="h-7 w-7 shrink-0 rounded-md bg-brand-500/20 text-brand-300 hover:bg-brand-500/30 disabled:opacity-30 disabled:hover:bg-brand-500/20 flex items-center justify-center transition-colors"
          aria-label="Send"
        >
          <Send size={12} />
        </button>
      </div>
    </div>
  )
}
