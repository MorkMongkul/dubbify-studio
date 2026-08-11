// src/components/overlays/OverlaysPanel.tsx
// Left-sidebar tab for the CapCut-style overlay editor — add/remove layers
// and adjust opacity/z-order here; drag/resize/position happens live on the
// video canvas itself (OverlayCanvas.tsx).
import { useState } from 'react'
import { ImagePlus, Type, Square, Trash2, ChevronUp, ChevronDown, UploadCloud, Star, BookmarkPlus } from 'lucide-react'
import { toast } from 'sonner'
import type { Project } from '@/types'
import { useEditorStore } from '@/store/editorStore'
import {
  useOverlays, useCreateImageOverlay, useCreateOverlayFromLogo,
  useCreateSubtitleOverlay, useCreateShapeOverlay, useUpdateOverlay, useDeleteOverlay,
  useOverlayTemplates, useCreateOverlayTemplate, useUpdateOverlayTemplate,
  useDeleteOverlayTemplate, useApplyOverlayTemplate,
} from '@/hooks/useApi'
import { cn } from '@/lib/utils'

interface OverlaysPanelProps {
  jobId: string
  project?: Project
  onUploadProjectLogo: (file: File | null) => void
  onRemoveProjectLogo: () => void
  projectLogoUploading?: boolean
}

export function OverlaysPanel({
  jobId, project, onUploadProjectLogo, onRemoveProjectLogo, projectLogoUploading,
}: OverlaysPanelProps) {
  const { data: overlays = [], isLoading } = useOverlays(jobId)
  const selectedOverlayId = useEditorStore((s) => s.selectedOverlayId)
  const setSelectedOverlay = useEditorStore((s) => s.setSelectedOverlay)

  const createImage = useCreateImageOverlay()
  const createFromLogo = useCreateOverlayFromLogo()
  const createSubtitle = useCreateSubtitleOverlay()
  const createShape = useCreateShapeOverlay()
  const updateOverlay = useUpdateOverlay()
  const deleteOverlay = useDeleteOverlay()

  const hasSubtitleOverlay = overlays.some((o) => o.type === 'subtitle')
  const sorted = [...overlays].sort((a, b) => b.z_index - a.z_index) // top layer first
  const selected = overlays.find((o) => o.id === selectedOverlayId) ?? null

  const handleAddImage = (file: File | null) => {
    if (!file) return
    createImage.mutate({ jobId, file }, {
      onSuccess: (created) => setSelectedOverlay(created.id),
      onError: () => toast.error('Failed to add image'),
    })
  }

  // Swap with the ADJACENT layer in the displayed stack. Picking the neighbour
  // by scanning `sorted` (descending) for the first z_index above the current
  // one found the topmost layer, not the one directly above, so "bring forward"
  // jumped a layer straight to the top and scrambled everything in between.
  const handleReorder = (index: number, direction: 'up' | 'down') => {
    const target = sorted[index]
    const neighbor = sorted[direction === 'up' ? index - 1 : index + 1]
    if (!target || !neighbor) return
    updateOverlay.mutate({ id: target.id, jobId, data: { z_index: neighbor.z_index } })
    updateOverlay.mutate({ id: neighbor.id, jobId, data: { z_index: target.z_index } })
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-3 gap-1.5">
        <label className="flex flex-col items-center justify-center gap-1 p-2 rounded border border-dashed border-zinc-700/60 hover:border-purple-500/50 hover:bg-purple-500/5 cursor-pointer transition-all text-[10px] text-zinc-400">
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            onChange={(e) => handleAddImage(e.target.files?.[0] ?? null)}
          />
          <ImagePlus size={13} />
          Image
        </label>
        <button
          onClick={() => hasSubtitleOverlay
            ? toast.info('Subtitle box already added')
            : createSubtitle.mutate(jobId, {
                onSuccess: (created) => setSelectedOverlay(created.id),
                onError: () => toast.error('Failed to add subtitles'),
              })
          }
          disabled={hasSubtitleOverlay}
          className="flex flex-col items-center justify-center gap-1 p-2 rounded border border-dashed border-zinc-700/60 hover:border-purple-500/50 hover:bg-purple-500/5 cursor-pointer transition-all text-[10px] text-zinc-400 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Type size={13} />
          Subtitles
        </button>
        <button
          onClick={() => createShape.mutate({ jobId }, {
            onSuccess: (created) => setSelectedOverlay(created.id),
            onError: () => toast.error('Failed to add box'),
          })}
          title="A plain box — solid color or blur — useful for covering something already in the video, like a burned-in subtitle"
          className="flex flex-col items-center justify-center gap-1 p-2 rounded border border-dashed border-zinc-700/60 hover:border-purple-500/50 hover:bg-purple-500/5 cursor-pointer transition-all text-[10px] text-zinc-400"
        >
          <Square size={13} />
          Box
        </button>
      </div>

      {/* Project-level saved logo — set once, reusable across every job/export in this project */}
      {project?.logo_url ? (
        <div className="flex items-center gap-2 px-1">
          <img src={project.logo_url} alt="Project logo" className="h-6 w-6 rounded object-contain bg-zinc-800/50 border border-zinc-700/50" />
          <button
            onClick={() => createFromLogo.mutate(jobId, {
              onSuccess: (created) => setSelectedOverlay(created.id),
              onError: () => toast.error('Failed to add logo'),
            })}
            className="flex items-center gap-1 text-[10px] text-purple-400 hover:text-purple-300"
          >
            <UploadCloud size={11} /> Use on this video
          </button>
          <label className="text-[10px] text-zinc-500 hover:text-zinc-300 cursor-pointer ml-auto">
            <input
              type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
              onChange={(e) => onUploadProjectLogo(e.target.files?.[0] ?? null)}
            />
            Replace
          </label>
          <button onClick={onRemoveProjectLogo} className="text-zinc-500 hover:text-red-400">
            <Trash2 size={11} />
          </button>
        </div>
      ) : (
        <label className="flex items-center justify-center gap-1.5 p-1.5 rounded text-[10px] text-zinc-500 hover:text-purple-300 hover:bg-purple-500/5 cursor-pointer transition-colors">
          <input
            type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
            onChange={(e) => onUploadProjectLogo(e.target.files?.[0] ?? null)}
          />
          <UploadCloud size={11} />
          {projectLogoUploading ? 'Uploading…' : 'Save a project logo (reusable across videos)'}
        </label>
      )}

      <div className="h-px bg-zinc-800/60 my-1" />

      <TemplatesSection jobId={jobId} hasOverlays={overlays.length > 0} />

      <div className="h-px bg-zinc-800/60 my-1" />

      {isLoading ? (
        <div className="text-[11px] text-zinc-600 text-center py-4">Loading…</div>
      ) : sorted.length === 0 ? (
        <div className="text-[11px] text-zinc-600 text-center py-4">
          No elements yet — add an image or subtitles above
        </div>
      ) : (
        <div className="space-y-1">
          {sorted.map((ov, i) => (
            <div
              key={ov.id}
              onClick={() => setSelectedOverlay(ov.id)}
              className={cn(
                'flex items-center gap-2 p-1.5 rounded border cursor-pointer transition-all',
                selectedOverlayId === ov.id
                  ? 'bg-purple-500/10 border-purple-500/40'
                  : 'bg-zinc-800/30 border-zinc-800/40 hover:bg-zinc-800/60'
              )}
            >
              {ov.type === 'image' ? (
                <img src={ov.media_url ?? undefined} alt="" className="h-6 w-6 rounded object-cover bg-zinc-900 shrink-0" />
              ) : (
                <div className="h-6 w-6 rounded bg-zinc-900 flex items-center justify-center shrink-0">
                  {ov.type === 'subtitle' ? <Type size={11} className="text-zinc-500" /> : <Square size={11} className="text-zinc-500" />}
                </div>
              )}
              <span className="text-[11px] font-medium text-zinc-200 flex-1 truncate">
                {ov.type === 'image' ? 'Image' : ov.type === 'subtitle' ? 'Subtitles' : ov.blur ? 'Blur box' : 'Box'}
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); handleReorder(i, 'up') }}
                disabled={i === 0}
                className="text-zinc-500 hover:text-zinc-200 disabled:opacity-20 disabled:cursor-not-allowed"
                title="Bring forward"
              >
                <ChevronUp size={12} />
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); handleReorder(i, 'down') }}
                disabled={i === sorted.length - 1}
                className="text-zinc-500 hover:text-zinc-200 disabled:opacity-20 disabled:cursor-not-allowed"
                title="Send backward"
              >
                <ChevronDown size={12} />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  deleteOverlay.mutate({ id: ov.id, jobId })
                  if (selectedOverlayId === ov.id) setSelectedOverlay(null)
                }}
                className="text-zinc-500 hover:text-red-400"
                title="Delete"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Properties — one dedicated spot, contents swap based on which
          element (if any) is selected above. Keeps the list itself a
          uniform height regardless of selection, and gives every element
          type room to grow its own settings later without reshuffling
          the list layout. */}
      {selected && (
        <>
          <div className="h-px bg-zinc-800/60 my-1" />
          <div className="p-2 rounded border border-purple-500/30 bg-purple-500/5 space-y-2">
            <span className="text-[9px] font-semibold uppercase tracking-widest text-purple-400/80">
              {selected.type === 'image' ? 'Image properties' : selected.type === 'subtitle' ? 'Subtitle properties' : 'Box properties'}
            </span>

            {selected.type === 'image' && (
              <div className="flex items-center gap-1.5">
                <span className="text-[9px] text-zinc-500 w-10 shrink-0">Opacity</span>
                <input
                  type="range" min={0} max={1} step={0.05}
                  defaultValue={selected.opacity}
                  onMouseUp={(e) => updateOverlay.mutate({ id: selected.id, jobId, data: { opacity: Number(e.currentTarget.value) } })}
                  onTouchEnd={(e) => updateOverlay.mutate({ id: selected.id, jobId, data: { opacity: Number(e.currentTarget.value) } })}
                  className="w-full"
                />
              </div>
            )}

            {selected.type === 'subtitle' && (
              <>
                <div className="flex items-center gap-3">
                  <ColorSwatches
                    label="Fill"
                    value={selected.color}
                    onChange={(color) => updateOverlay.mutate({ id: selected.id, jobId, data: { color } })}
                  />
                  <ColorSwatches
                    label="Outline"
                    value={selected.outline_color}
                    onChange={(outline_color) => updateOverlay.mutate({ id: selected.id, jobId, data: { outline_color } })}
                  />
                </div>
                <div className="flex items-center gap-3">
                  <ColorSwatches
                    label="Background"
                    value={selected.background_color}
                    onChange={(background_color) => updateOverlay.mutate({ id: selected.id, jobId, data: { background_color } })}
                    allowNone
                  />
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[9px] text-zinc-500 shrink-0">Size</span>
                  <input
                    type="range" min={20} max={72} step={2}
                    defaultValue={selected.font_size}
                    onMouseUp={(e) => updateOverlay.mutate({ id: selected.id, jobId, data: { font_size: Number(e.currentTarget.value) } })}
                    onTouchEnd={(e) => updateOverlay.mutate({ id: selected.id, jobId, data: { font_size: Number(e.currentTarget.value) } })}
                    className="flex-1 min-w-0"
                    title="Font size, in real video pixels — identical on every segment"
                  />
                </div>
                <p className="text-[9px] text-zinc-500 leading-relaxed">
                  Height is automatic: longer lines wrap and grow upward from the box's
                  bottom edge at this same size. On the video, drag the side handles to
                  set how wide the text column is.
                </p>
              </>
            )}

            {selected.type === 'shape' && (
              <>
                <div className="grid grid-cols-2 gap-1.5">
                  <button
                    onClick={() => updateOverlay.mutate({ id: selected.id, jobId, data: { blur: false } })}
                    className={cn(
                      'py-1 rounded text-[10px] font-medium transition-all border',
                      !selected.blur ? 'bg-purple-500/20 border-purple-500/50 text-purple-300' : 'border-zinc-700 text-zinc-400 hover:border-zinc-500'
                    )}
                  >
                    Solid color
                  </button>
                  <button
                    onClick={() => updateOverlay.mutate({ id: selected.id, jobId, data: { blur: true } })}
                    className={cn(
                      'py-1 rounded text-[10px] font-medium transition-all border',
                      selected.blur ? 'bg-purple-500/20 border-purple-500/50 text-purple-300' : 'border-zinc-700 text-zinc-400 hover:border-zinc-500'
                    )}
                  >
                    Blur
                  </button>
                </div>

                {selected.blur ? (
                  <p className="text-[9px] text-zinc-500 leading-relaxed">
                    Blurs whatever's in the video underneath this box — handy for hiding a burned-in original subtitle without a flat color block.
                  </p>
                ) : (
                  <>
                    <ColorSwatches
                      label="Fill"
                      value={selected.color}
                      onChange={(color) => updateOverlay.mutate({ id: selected.id, jobId, data: { color } })}
                    />
                    <div className="flex items-center gap-1.5">
                      <span className="text-[9px] text-zinc-500 w-10 shrink-0">Opacity</span>
                      <input
                        type="range" min={0} max={1} step={0.05}
                        defaultValue={selected.opacity}
                        onMouseUp={(e) => updateOverlay.mutate({ id: selected.id, jobId, data: { opacity: Number(e.currentTarget.value) } })}
                        onTouchEnd={(e) => updateOverlay.mutate({ id: selected.id, jobId, data: { opacity: Number(e.currentTarget.value) } })}
                        className="w-full"
                      />
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// Saved overlay layouts ("brand kits") — snapshot every element on this
// episode once, then re-apply the identical layout to any other episode.
// The starred template is stamped automatically onto every new upload.
function TemplatesSection({ jobId, hasOverlays }: { jobId: string; hasOverlays: boolean }) {
  const { data: templates = [] } = useOverlayTemplates()
  const createTemplate = useCreateOverlayTemplate()
  const updateTemplate = useUpdateOverlayTemplate()
  const deleteTemplate = useDeleteOverlayTemplate()
  const applyTemplate = useApplyOverlayTemplate()
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')

  const handleSave = () => {
    const trimmed = name.trim()
    if (!trimmed) return
    createTemplate.mutate(
      // First template becomes the default automatically, so new episodes
      // start picking it up without an extra step.
      { name: trimmed, fromJobId: jobId, setDefault: templates.length === 0 },
      {
        onSuccess: () => {
          toast.success(`Template "${trimmed}" saved${templates.length === 0 ? ' and set as default for new episodes' : ''}`)
          setSaving(false)
          setName('')
        },
        onError: () => toast.error('Failed to save template'),
      },
    )
  }

  return (
    <div className="space-y-1">
      <span className="text-[9px] font-semibold uppercase tracking-widest text-zinc-500 px-1">
        Layout templates
      </span>

      {templates.map((t) => (
        <div
          key={t.id}
          className="flex items-center gap-1.5 p-1.5 rounded border bg-zinc-800/30 border-zinc-800/40"
        >
          <button
            onClick={() => updateTemplate.mutate({ id: t.id, data: { is_default: !t.is_default } })}
            className={cn('shrink-0', t.is_default ? 'text-yellow-400' : 'text-zinc-600 hover:text-zinc-300')}
            title={t.is_default ? 'Default — applied automatically to every new episode' : 'Make default for new episodes'}
          >
            <Star size={12} fill={t.is_default ? 'currentColor' : 'none'} />
          </button>
          <span className="text-[11px] font-medium text-zinc-200 flex-1 truncate" title={`${t.item_count} element(s)`}>
            {t.name}
          </span>
          <button
            onClick={() => applyTemplate.mutate({ id: t.id, jobId }, {
              onSuccess: (created) => toast.success(`${created.length} element(s) added from "${t.name}"`),
              onError: () => toast.error('Failed to apply template'),
            })}
            disabled={applyTemplate.isPending}
            className="text-[10px] text-purple-400 hover:text-purple-300 disabled:opacity-40 shrink-0"
          >
            Apply
          </button>
          <button
            onClick={() => deleteTemplate.mutate(t.id, {
              onError: () => toast.error('Failed to delete template'),
            })}
            className="text-zinc-500 hover:text-red-400 shrink-0"
            title="Delete template (episodes it was applied to keep their elements)"
          >
            <Trash2 size={11} />
          </button>
        </div>
      ))}

      {saving ? (
        <div className="flex items-center gap-1.5">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSave()
              if (e.key === 'Escape') { setSaving(false); setName('') }
            }}
            placeholder="Template name…"
            className="flex-1 min-w-0 bg-zinc-900 border border-zinc-700 rounded px-1.5 py-1 text-[11px] text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-purple-500/60"
          />
          <button
            onClick={handleSave}
            disabled={!name.trim() || createTemplate.isPending}
            className="text-[10px] text-purple-400 hover:text-purple-300 disabled:opacity-40 shrink-0"
          >
            {createTemplate.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      ) : (
        <button
          onClick={() => hasOverlays
            ? setSaving(true)
            : toast.info('Add elements to the video first, then save them as a template')}
          className="w-full flex items-center justify-center gap-1.5 p-1.5 rounded text-[10px] text-zinc-500 hover:text-purple-300 hover:bg-purple-500/5 transition-colors"
          title="Snapshot every element on this episode as a reusable layout"
        >
          <BookmarkPlus size={11} />
          Save current layout as template
        </button>
      )}
    </div>
  )
}

const SWATCH_COLORS = ['white', 'yellow', 'black'] as const

function ColorSwatches({
  label, value, onChange, allowNone,
}: { label: string; value: string; onChange: (color: string) => void; allowNone?: boolean }) {
  return (
    <div className="flex items-center gap-1" title={label}>
      <span className="text-[9px] text-zinc-500 mr-0.5">{label}</span>
      {allowNone && (
        <button
          onClick={() => onChange('')}
          className={cn(
            'h-4 w-4 rounded-full border-2 transition-all shrink-0 relative overflow-hidden',
            'bg-[repeating-linear-gradient(45deg,#3f3f46_0_2px,transparent_2px_4px)]',
            value === '' ? 'border-purple-500 scale-110' : 'border-zinc-700 hover:border-zinc-500'
          )}
          title="None"
        />
      )}
      {SWATCH_COLORS.map((c) => (
        <button
          key={c}
          onClick={() => onChange(c)}
          className={cn(
            'h-4 w-4 rounded-full border-2 transition-all shrink-0',
            value === c ? 'border-purple-500 scale-110' : 'border-zinc-700 hover:border-zinc-500'
          )}
          style={{ background: c }}
          title={c}
        />
      ))}
    </div>
  )
}
