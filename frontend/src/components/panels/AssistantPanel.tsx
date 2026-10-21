// src/components/panels/AssistantPanel.tsx
// Assistant tab of the dock — a project-scoped chat that can search this
// project's segments across every episode and propose translation edits.
// Edit approval has two modes (mirrors an agentic coding tool's auto/ask
// toggle): "manual" stages every edit for approval below the chat before it
// touches a segment; "auto" applies immediately but still logs an
// approved-status row so there's an audit trail to review or revert from.
// Your messages are bubbles on the right; the Assistant's replies are markdown,
// shown as open text across the panel. What you send shows at once, with
// "Thinking…" where the reply will appear (useSendChatMessage). The line your
// text cursor was last in (editorStore.assistantLineId) is "this line": a tag
// above the box shows it, and it goes with the message. A project has several
// chats: History lists them (newest first) and New chat starts one; each sends
// only its own messages to the AI. editorStore.assistantThreads remembers the
// open one per project.
import { useEffect, useRef, useState } from 'react'
import { useIsMutating } from '@tanstack/react-query'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Bot, Send, Check, X, Search, ListTree, Pencil, Loader2, History, SquarePen, ChevronLeft, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import type { AxiosError } from 'axios'
import {
  useProject, useUpdateProject, useSegments,
  useChatThreads, useChatMessages, useSendChatMessage, useDeleteChatThread,
  usePendingEdits, useApproveEdit, useRejectEdit,
} from '@/hooks/useApi'
import { chatDateLabel } from '@/lib/chatMessages'
import { useEditorStore } from '@/store/editorStore'
import type { ChatEditMode, ChatLineRef, ChatMessage, ChatProposedEdit, ChatThread } from '@/types'
import { PanelHeader, PanelEmptyState } from './PanelShell'
import { cn, formatTime } from '@/lib/utils'

interface AssistantPanelProps {
  projectId?: string
  /** The open episode, whose lines can be "this line" */
  jobId?: string
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

// Tables, lists and the rest of GitHub-style markdown; no pictures (a reply
// could point one anywhere), and links open outside the editor
const REMARK_PLUGINS = [remarkGfm]
const MARKDOWN_COMPONENTS: Components = {
  a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer">{children}</a>,
}

function lineLabel(line: ChatLineRef): string {
  return [line.episode ? `Ep ${line.episode}` : '', formatTime(line.time)].filter(Boolean).join(' · ')
}

function ChatBubble({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="flex flex-col items-end gap-0.5">
        {message.line_ref && (
          <span className="text-[9.5px] text-ink-500 truncate max-w-[85%]" title={message.line_ref.source_text}>
            {lineLabel(message.line_ref)} · <span lang="zh-Hans">{message.line_ref.source_text}</span>
          </span>
        )}
        <div className="max-w-[85%] rounded-lg px-2.5 py-1.5 text-[11px] leading-relaxed whitespace-pre-wrap break-words bg-brand-500/15 text-ink-100">
          {message.text}
        </div>
      </div>
    )
  }
  return (
    <div className="chat-md px-1 text-[11px] leading-relaxed break-words text-ink-200">
      <Markdown
        remarkPlugins={REMARK_PLUGINS}
        components={MARKDOWN_COMPONENTS}
        disallowedElements={['img']}
        unwrapDisallowed
      >
        {message.text}
      </Markdown>
    </div>
  )
}

type SendVariables = { projectId?: string; threadId?: string | null } | undefined

/** The project's chats over the panel: open one, start one, delete one (asks first). */
function ChatHistory({ projectId, threads, openId, onOpen, onClose }: {
  projectId: string; threads: ChatThread[]; openId: string | null
  onOpen: (threadId: string | null) => void; onClose: () => void
}) {
  const remove = useDeleteChatThread()
  const setThread = useEditorStore((s) => s.setAssistantThread)
  const [now] = useState(() => new Date())
  const [confirming, setConfirming] = useState<string | null>(null)
  useEffect(() => {
    if (!confirming) return
    const t = setTimeout(() => setConfirming(null), 4000)
    return () => clearTimeout(t)
  }, [confirming])

  const del = (thread: ChatThread) => remove.mutate(
    { threadId: thread.id, projectId },
    {
      // The open chat went: open the one used last instead
      onSuccess: () => { if (thread.id === openId) setThread(projectId, undefined) },
      onError: (e) => toast.error((e as Error).message || "Couldn't delete the chat"),
    },
  )

  return (
    <div className="absolute inset-0 z-10 flex flex-col bg-ink-900">
      <div className="h-9 shrink-0 flex items-center gap-2 px-2 border-b border-ink-800/50">
        <button type="button" onClick={onClose} className="flex items-center gap-0.5 text-[11px] text-ink-400 hover:text-white transition-colors">
          <ChevronLeft size={14} /> Chats
        </button>
        <button
          type="button"
          onClick={() => onOpen(null)}
          className="ml-auto flex items-center gap-1 rounded-md bg-brand px-2 py-0.5 text-[10.5px] font-semibold text-white hover:bg-brand-hover transition-colors"
        >
          <SquarePen size={11} /> New chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {threads.length === 0 ? (
          <p className="px-3 py-6 text-center text-[10px] text-ink-600">No chats yet</p>
        ) : threads.map((t) => (
          <div
            key={t.id}
            className={cn(
              'flex items-center gap-2 border-l-2 pl-3 pr-2 py-2 hover:bg-white/5 transition-colors',
              t.id === openId ? 'border-brand-500 bg-white/5' : 'border-transparent',
            )}
          >
            <button type="button" onClick={() => onOpen(t.id)} className="flex-1 min-w-0 text-left">
              <p className="truncate text-[11.5px] text-ink-100">{t.title || 'New chat'}</p>
              <p className="text-[10px] text-ink-500">
                {chatDateLabel(t.updated_at, now)} · {t.message_count} {t.message_count === 1 ? 'message' : 'messages'}
              </p>
            </button>
            <button
              type="button"
              onClick={() => (confirming === t.id ? del(t) : setConfirming(t.id))}
              disabled={remove.isPending}
              title={confirming === t.id ? 'Click again to delete this chat' : 'Delete this chat'}
              className={cn(
                'shrink-0 rounded px-1 py-0.5 text-[10px] transition-colors',
                confirming === t.id ? 'font-semibold text-red-400 hover:bg-red-500/10' : 'text-ink-600 hover:text-red-400',
              )}
            >
              {confirming === t.id ? 'Delete?' : <Trash2 size={12} />}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

export function AssistantPanel({ projectId, jobId }: AssistantPanelProps) {
  const { data: project } = useProject(projectId ?? null)
  const updateProject = useUpdateProject()
  // The open chat: the one chosen in History, or New chat (null), else the one used last
  const { data: threads, isLoading: loadingThreads } = useChatThreads(projectId ?? null)
  const chosen = useEditorStore((s) => (projectId ? s.assistantThreads[projectId] : undefined))
  const setThread = useEditorStore((s) => s.setAssistantThread)
  const threadId: string | null = chosen !== undefined ? chosen : (threads?.[0]?.id ?? null)
  const [showHistory, setShowHistory] = useState(false)
  const { data: messages = [], isLoading: loadingMessages } = useChatMessages(projectId ?? null, threadId)
  const sendMessage = useSendChatMessage()
  const { data: pendingEdits = [] } = usePendingEdits(projectId ?? null)
  const approveEdit = useApproveEdit()
  const rejectEdit = useRejectEdit()
  // The turn runs on when this panel closes, so ask the mutation cache rather
  // than this panel's own useMutation. One reply at a time per project (Send
  // waits); "Thinking…" shows in the chat being answered.
  const busy = useIsMutating({
    mutationKey: ['chat-send'],
    predicate: (m) => (m.state.variables as SendVariables)?.projectId === projectId,
  }) > 0
  const thinking = useIsMutating({
    mutationKey: ['chat-send'],
    predicate: (m) => {
      const vars = m.state.variables as SendVariables
      return vars?.projectId === projectId && (vars?.threadId ?? null) === threadId
    },
  }) > 0

  // A suggestion's line, when it's in the open episode: select it and play from
  // there, so the transcript shows (and scrolls to) the line that changes
  const setActiveSegment = useEditorStore((s) => s.setActiveSegment)
  const setCurrentTime = useEditorStore((s) => s.setCurrentTime)
  const goToLine = (edit: ChatProposedEdit) => {
    if (!edit.line || edit.line.job_id !== jobId) return
    setActiveSegment(edit.segment_id)
    setCurrentTime(edit.line.time)
  }

  // "This line": only while it's a line of the open episode
  const lineId = useEditorStore((s) => s.assistantLineId)
  const setLineId = useEditorStore((s) => s.setAssistantLineId)
  const { data: lines = [] } = useSegments(jobId ?? null)
  const lineSeg = lineId ? lines.find((s) => s.id === lineId) : undefined

  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages.length, thinking, threadId])

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
    if (!text || busy) return
    setDraft('')
    // The server fills in the episode number; the stand-in shows the time
    const line: ChatLineRef | undefined = lineSeg && {
      segment_id: lineSeg.id, episode: 0, time: lineSeg.start_time, speaker: '', speaker_tag: '',
      source_text: lineSeg.source_text ?? '', khmer_text: lineSeg.khmer_text ?? '',
    }
    sendMessage.mutate(
      { projectId, threadId, text, line },
      {
        onError: (err: Error) => {
          toast.error(err.message || 'Assistant request failed')
          setDraft((d) => d || text)   // back in the box, to send again
          if (line && (err as AxiosError).response?.status === 404) setLineId(null)   // the line is gone
        },
      }
    )
  }

  return (
    <div className="h-full flex flex-col min-h-0">
      <PanelHeader
        icon={Bot}
        title="Assistant"
        action={
          <>
            <button
              type="button"
              onClick={() => setShowHistory(true)}
              title="Chat history"
              aria-label="Chat history"
              className="h-6 w-6 flex items-center justify-center rounded text-ink-500 hover:text-white hover:bg-white/5 transition-colors"
            >
              <History size={13} />
            </button>
            <button
              type="button"
              onClick={() => { setThread(projectId, null); setShowHistory(false) }}
              title="New chat"
              aria-label="New chat"
              className="h-6 w-6 flex items-center justify-center rounded text-ink-500 hover:text-white hover:bg-white/5 transition-colors"
            >
              <SquarePen size={13} />
            </button>
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
          </>
        }
      />

      <div className="relative flex-1 flex flex-col min-h-0">
        {showHistory && (
          <ChatHistory
            projectId={projectId}
            threads={threads ?? []}
            openId={threadId}
            onOpen={(id) => { setThread(projectId, id); setShowHistory(false) }}
            onClose={() => setShowHistory(false)}
          />
        )}

        {pendingEdits.length > 0 && (
          <div className="border-b border-ink-800/50 bg-ink-950/40 max-h-[40%] overflow-y-auto shrink-0">
            <div className="px-3 pt-2 pb-1 text-[9px] font-semibold text-ink-500 uppercase tracking-wide">
              Pending edits ({pendingEdits.length})
            </div>
            <div className="px-2 pb-2 space-y-1.5">
              {pendingEdits.map((edit) => (
                <div key={edit.id} className="p-2 rounded bg-ink-900/80 border border-ink-800/60 space-y-1">
                  {edit.line && (
                    // Which line it changes; in the open episode, a click goes there
                    <button
                      type="button"
                      onClick={() => goToLine(edit)}
                      disabled={edit.line.job_id !== jobId}
                      title={edit.line.job_id === jobId ? 'Go to this line' : `In episode ${edit.line.episode}`}
                      className="flex w-full min-w-0 items-center gap-1 text-left text-[9.5px] text-ink-500 enabled:hover:text-brand-300 transition-colors"
                    >
                      <span className="shrink-0 tabular-nums">Ep {edit.line.episode} · {formatTime(edit.line.time)} ·</span>
                      <span lang="zh-Hans" className="truncate">{edit.line.source_text}</span>
                    </button>
                  )}
                  <p className="text-[10px] text-ink-500 line-through decoration-ink-700 truncate">{edit.old_khmer_text}</p>
                  <p className="text-[11px] text-ink-200 truncate">{edit.new_khmer_text}</p>
                  <p className="text-[9px] text-ink-600 leading-snug">{edit.reason}</p>
                  <div className="flex gap-1 pt-0.5">
                    <button
                      onClick={() => approveEdit.mutate({ editId: edit.id, projectId }, { onSuccess: () => goToLine(edit) })}
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

        <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
          {loadingMessages || (loadingThreads && chosen === undefined) ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 size={16} className="animate-spin text-ink-600" />
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-6 text-center">
              <Bot size={18} className="text-ink-700 mb-2" />
              <p className="text-[10px] text-ink-600">Ask about this project's translations</p>
              <p className="text-[9px] text-ink-700 mt-0.5 max-w-[220px] leading-normal">
                e.g. "How was 老公 translated in episode 2?" — or click into a line and ask "Make this less formal"
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
          {thinking && (
            // Where the reply will appear
            <div className="px-1 text-[11px] leading-relaxed">
              <span className="shimmer-text font-medium">Thinking…</span>
            </div>
          )}
        </div>

        {lineSeg && (
          <div className="px-2 pt-1.5 border-t border-ink-800/50 shrink-0">
            <span className="inline-flex max-w-full items-center gap-1.5 rounded-md bg-brand-500/15 px-1.5 py-0.5 text-[10px] text-brand-300">
              <span className="font-semibold shrink-0">This line</span>
              <span className="shrink-0 tabular-nums">{formatTime(lineSeg.start_time)}</span>
              <span lang="zh-Hans" className="truncate text-ink-400">{lineSeg.source_text}</span>
              <button
                type="button"
                onClick={() => setLineId(null)}
                aria-label="Not about this line"
                title="Not about this line"
                className="shrink-0 text-ink-500 hover:text-white transition-colors"
              >
                <X size={10} />
              </button>
            </span>
          </div>
        )}
        <div className={cn('p-2 flex items-end gap-1.5 shrink-0', !lineSeg && 'border-t border-ink-800/50')}>
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
            disabled={!draft.trim() || busy}
            className="h-7 w-7 shrink-0 rounded-md bg-brand-500/20 text-brand-300 hover:bg-brand-500/30 disabled:opacity-30 disabled:hover:bg-brand-500/20 flex items-center justify-center transition-colors"
            aria-label="Send"
          >
            <Send size={12} />
          </button>
        </div>
      </div>
    </div>
  )
}
