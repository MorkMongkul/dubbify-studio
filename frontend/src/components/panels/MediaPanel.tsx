// src/components/panels/MediaPanel.tsx
// The Media tab (0.4.0), like CapCut's Media: the project's images and videos —
// import them or drop them in, add one at the playhead (+) or drag it onto the
// timeline (MediaTracks reads MEDIA_DRAG) — and media templates, which are only
// ever applied by hand: a new upload is dubbed first, then gets its template.
import { useCallback, useRef, useState, type DragEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Check, Clapperboard, LayoutTemplate, Loader2, Pencil, Plus, TriangleAlert, Trash2, Upload, X } from 'lucide-react'
import type { AxiosError } from 'axios'
import { PanelEmptyState, PanelHeader } from '@/components/panels/PanelShell'
import { Tooltip } from '@/components/ui/Tooltip'
import {
  useApplyMediaTemplate, useDeleteMedia, useDeleteMediaTemplate, useMediaAssets, useMediaClips, useMediaTemplates,
  useRenameMediaTemplate, useSaveMediaTemplate, useSetMediaClips, useUploadMedia,
} from '@/hooks/useApi'
import { MEDIA_DRAG, addAt, defaultClip, mediaSwitchesOf } from '@/lib/media'
import { recordMediaChange } from '@/lib/historyHelpers'
import { useEditorStore } from '@/store/editorStore'
import { cn, formatTime } from '@/lib/utils'
import type { MediaAsset, MediaClipList, MediaTemplate } from '@/types'

const ACCEPT = 'image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/x-m4v,video/x-matroska,video/webm,image/gif,.mov,.mkv,.m4v'
const iconButton = 'flex items-center justify-center h-7 w-7 rounded-md bg-white/[0.04] hover:bg-white/10 text-white/45 hover:text-white transition-colors disabled:opacity-40'
// A checkerboard behind thumbnails, so transparency shows as transparency
const CHECKER = { backgroundImage: 'conic-gradient(#262b40 25%, #1c2033 0 50%, #262b40 0 75%, #1c2033 0)', backgroundSize: '12px 12px' }

// Each import's row (never crypto.randomUUID: Safari before 15.4 has none)
let importCount = 0

const errorText = (e: unknown, fallback: string) =>
  ((e as AxiosError<{ detail?: string }>)?.response?.data?.detail) || fallback

export function MediaPanel({ projectId, jobId }: { projectId?: string; jobId?: string }) {
  if (!projectId) {
    return (
      <div className="h-full flex flex-col min-h-0">
        <PanelHeader icon={Clapperboard} title="Media" />
        <PanelEmptyState icon={Clapperboard} title="No project open" hint="Media belongs to a project and is shared by its episodes." />
      </div>
    )
  }
  return <MediaLibrary projectId={projectId} jobId={jobId} />
}

function MediaLibrary({ projectId, jobId }: { projectId: string; jobId?: string }) {
  const qc = useQueryClient()
  const { data: assets = [] } = useMediaAssets(projectId)
  const { data: clipList } = useMediaClips(jobId)
  const upload = useUploadMedia()
  const remove = useDeleteMedia()
  // mutateAsync, never mutate's own callbacks: TanStack calls those for the last call only,
  // so a second import or edit in flight lost the first one's row, error or undo step
  const { mutateAsync: setClips } = useSetMediaClips()
  const [uploads, setUploads] = useState<{ key: string; name: string; progress: number }[]>([])
  const [dragOver, setDragOver] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const used = new Set((clipList?.clips ?? []).map((c) => c.asset_id))

  const importFiles = useCallback((files: File[]) => {
    for (const file of files) {
      const key = `import-${++importCount}`
      setUploads((u) => [...u, { key, name: file.name, progress: 0 }])
      upload.mutateAsync({
        projectId, file,
        onProgress: (progress) => setUploads((u) => u.map((x) => (x.key === key ? { ...x, progress } : x))),
      })
        .catch((e) => toast.error(errorText(e, `Couldn't import ${file.name}`)))
        .finally(() => setUploads((u) => u.filter((x) => x.key !== key)))
    }
  }, [projectId, upload])

  // Files dropped from the Mac (a tile dragged inside the editor carries MEDIA_DRAG instead)
  const isFileDrag = (e: DragEvent) => Array.from(e.dataTransfer.types).includes('Files')
  const onDragOver = (e: DragEvent) => {
    if (!isFileDrag(e)) return
    e.preventDefault()
    e.stopPropagation()     // not the editor's own "drop a video to import"
    setDragOver(true)
  }
  const onDrop = (e: DragEvent) => {
    if (!isFileDrag(e)) return
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    importFiles(Array.from(e.dataTransfer.files))
  }

  const addAtPlayhead = (asset: MediaAsset) => {
    // The cache, not this render's list: a second + right after the first adds beside it
    const before = jobId ? qc.getQueryData<MediaClipList>(['media-clips', jobId]) : undefined
    if (!jobId || !before) return
    const { currentTime, duration, videoSize } = useEditorStore.getState()
    const frame = videoSize ?? { width: asset.width, height: asset.height }
    const locked = new Set(Object.keys(before.track_flags).map(Number).filter((l) => mediaSwitchesOf(before.track_flags, l).locked))
    const clip = addAt(defaultClip(asset, currentTime, duration, 0, frame), before.clips, locked)
    setClips({ jobId, clips: [...before.clips, clip] })
      .then((stored) => recordMediaChange(qc, jobId, before, stored, `Add ${asset.name}`))
      .catch((e) => toast.error(errorText(e, `Couldn't add ${asset.name}`)))
  }

  return (
    <div className="h-full flex flex-col min-h-0" onDragOver={onDragOver} onDragLeave={() => setDragOver(false)} onDrop={onDrop}>
      <PanelHeader
        icon={Clapperboard}
        title="Media"
        count={assets.length || undefined}
        action={
          <button
            onClick={() => fileInput.current?.click()}
            className="flex items-center gap-1 h-6 px-2 rounded-md bg-brand/15 hover:bg-brand/25 text-brand-200 text-[10.5px] font-semibold transition-colors"
          >
            <Upload size={11} /> Import
          </button>
        }
      />
      <input
        ref={fileInput}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => { importFiles(Array.from(e.target.files ?? [])); e.target.value = '' }}
      />
      <div className={cn('flex-1 overflow-y-auto p-2.5 space-y-3', dragOver && 'bg-brand/[0.06] outline-1 outline-dashed outline-brand-400/50 -outline-offset-4')}>
        {uploads.map((u) => (
          <div key={u.key} className="flex items-center gap-2 text-[10.5px] text-white/60">
            <Loader2 size={11} className="animate-spin text-brand-300 shrink-0" />
            <span className="truncate flex-1">{u.name}</span>
            <span className="tabular-nums">{Math.round(u.progress * 100)}%</span>
          </div>
        ))}
        {assets.length === 0 && uploads.length === 0 ? (
          <button
            onClick={() => fileInput.current?.click()}
            className="w-full rounded-lg border border-dashed border-white/15 hover:border-brand-400/50 hover:bg-brand/[0.04] p-6 flex flex-col items-center gap-2 text-center transition-colors"
          >
            <Upload size={18} className="text-white/35" />
            <span className="text-[11px] font-semibold text-white/70">Import images or videos</span>
            <span className="text-[10px] text-white/35 leading-normal">or drop them here. Transparent video (ProRes 4444 .mov) stays transparent.</span>
          </button>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-2">
            {assets.map((asset) => (
              <MediaTile
                key={asset.id}
                asset={asset}
                added={used.has(asset.id)}
                canAdd={!!jobId && !!clipList}
                onAdd={() => addAtPlayhead(asset)}
                onDelete={() => remove.mutateAsync({ assetId: asset.id, projectId })
                  .then(({ removed_clips, episodes }) => toast.success(removed_clips
                    ? `Deleted ${asset.name} and its ${removed_clips} clip${removed_clips === 1 ? '' : 's'} in ${episodes} episode${episodes === 1 ? '' : 's'}`
                    : `Deleted ${asset.name}`))
                  .catch((e) => toast.error(errorText(e, `Couldn't delete ${asset.name}`)))}
              />
            ))}
          </div>
        )}
        <TemplatesSection projectId={projectId} jobId={jobId} clipList={clipList} />
      </div>
    </div>
  )
}

function MediaTile({ asset, added, canAdd, onAdd, onDelete }: {
  asset: MediaAsset; added: boolean; canAdd: boolean; onAdd: () => void; onDelete: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const ready = asset.status === 'ready'
  return (
    <div
      className="group relative"
      draggable={ready}
      onDragStart={(e) => {
        e.dataTransfer.setData(MEDIA_DRAG, asset.id)
        e.dataTransfer.effectAllowed = 'copy'
      }}
    >
      <div className="relative aspect-[4/3] rounded-md overflow-hidden border border-white/[0.08] group-hover:border-brand-400/50 transition-colors" style={CHECKER}>
        {asset.thumb_url && <img src={asset.thumb_url} alt="" draggable={false} className="absolute inset-0 w-full h-full object-contain" />}
        {added && <span className="absolute top-1 left-1 text-[8.5px] font-semibold px-1 py-px rounded bg-black/70 text-white/90">Added</span>}
        {asset.kind === 'video' && asset.duration_secs != null && (
          <span className="absolute top-1 right-1 text-[8.5px] font-mono px-1 py-px rounded bg-black/70 text-white/85">{formatTime(asset.duration_secs)}</span>
        )}
        {asset.has_alpha && !asset.alpha_kept && (
          <Tooltip content="Its transparency isn't kept — export it as ProRes 4444 .mov" side="bottom">
            <span className="absolute bottom-1 left-1 text-amber-300"><TriangleAlert size={12} /></span>
          </Tooltip>
        )}
        {!ready && (
          <div className="absolute inset-0 bg-black/60 flex flex-col items-center justify-center gap-1 text-center px-1">
            {asset.status === 'preparing'
              ? <><Loader2 size={14} className="animate-spin text-brand-300" /><span className="text-[9px] text-white/70">Preparing…</span></>
              : <Tooltip content={asset.error || 'Unknown error'}><span className="text-[9px] text-red-300">Couldn't prepare</span></Tooltip>}
          </div>
        )}
        {confirming ? (
          <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center gap-1.5 px-1.5 text-center">
            <span className="text-[9.5px] text-white/85 leading-snug">
              {asset.used_in ? `Its clips in ${asset.used_in} episode${asset.used_in === 1 ? '' : 's'} go too` : 'Delete it?'}
            </span>
            <div className="flex gap-1">
              <button onClick={onDelete} className="h-6 px-2 rounded bg-red-500 hover:bg-red-400 text-white text-[10px] font-semibold">Delete</button>
              <button onClick={() => setConfirming(false)} className="h-6 w-6 rounded bg-white/10 hover:bg-white/20 text-white/70 flex items-center justify-center"><X size={11} /></button>
            </div>
          </div>
        ) : (
          <div className="absolute bottom-1 right-1 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <Tooltip content="Delete" side="bottom">
              <button aria-label={`Delete ${asset.name}`} onClick={() => setConfirming(true)} className={cn(iconButton, 'h-6 w-6 bg-black/60 hover:bg-red-500/40')}>
                <Trash2 size={11} />
              </button>
            </Tooltip>
            {ready && (
              <Tooltip content={canAdd ? 'Add at the playhead' : 'Open an episode first'} side="bottom">
                <button aria-label={`Add ${asset.name}`} disabled={!canAdd} onClick={onAdd} className={cn(iconButton, 'h-6 w-6 bg-brand/80 hover:bg-brand text-white')}>
                  <Plus size={12} />
                </button>
              </Tooltip>
            )}
          </div>
        )}
      </div>
      <p className="mt-1 text-[10px] text-white/60 truncate" title={asset.name}>{asset.name}</p>
    </div>
  )
}

function TemplatesSection({ projectId, jobId, clipList }: { projectId: string; jobId?: string; clipList?: MediaClipList }) {
  const qc = useQueryClient()
  const { data: templates = [] } = useMediaTemplates(projectId)
  const save = useSaveMediaTemplate()
  const apply = useApplyMediaTemplate()
  const [naming, setNaming] = useState<string | null>(null)
  const canSave = !!jobId && !!clipList?.clips.length

  const commitSave = () => {
    const name = naming?.trim()
    if (!name || !jobId) { setNaming(null); return }
    save.mutate({ projectId, jobId, name }, {
      onSuccess: () => { setNaming(null); toast.success(`Saved template ${name}`) },
      onError: (e) => toast.error(errorText(e, "Couldn't save the template")),
    })
  }

  const applyTemplate = (t: MediaTemplate) => {
    if (!jobId || !clipList) return
    const before = qc.getQueryData<MediaClipList>(['media-clips', jobId]) ?? clipList
    apply.mutateAsync({ templateId: t.id, jobId })
      .then(({ clips, skipped }) => {
        recordMediaChange(qc, jobId, before, { ...before, clips }, `Apply ${t.name}`)
        const added = clips.length - before.clips.length
        toast.success(`Added ${added} clip${added === 1 ? '' : 's'} from ${t.name}` +
          (skipped ? ` — ${skipped} skipped: their media was deleted` : ''))
      })
      .catch((e) => toast.error(errorText(e, `Couldn't apply ${t.name}`)))
  }

  return (
    <div className="pt-2 border-t border-white/[0.07]">
      <div className="flex items-center gap-1.5 mb-1.5">
        <LayoutTemplate size={11} className="text-white/40" />
        <span className="text-[10.5px] font-semibold text-white/60">Templates</span>
        <Tooltip content={canSave ? "Save this episode's media as a template" : 'Add media to this episode first'} side="bottom">
          <button
            disabled={!canSave}
            onClick={() => setNaming('')}
            className="ml-auto flex items-center gap-1 h-6 px-1.5 rounded-md text-[10px] text-white/55 hover:text-white hover:bg-white/10 disabled:opacity-40 transition-colors"
          >
            <Plus size={11} /> Save
          </button>
        </Tooltip>
      </div>
      {naming !== null && (
        <div className="flex items-center gap-1 mb-1.5">
          <input
            autoFocus
            value={naming}
            placeholder="Template name"
            onChange={(e) => setNaming(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') commitSave(); if (e.key === 'Escape') setNaming(null) }}
            className="flex-1 min-w-0 h-7 px-2 rounded-md bg-ink-950 border border-brand-400/50 text-[11px] text-white focus:outline-none"
          />
          <button onClick={commitSave} className={cn(iconButton, 'text-brand-300')} aria-label="Save template"><Check size={12} /></button>
          <button onClick={() => setNaming(null)} className={iconButton} aria-label="Cancel"><X size={12} /></button>
        </div>
      )}
      {templates.length === 0 && naming === null ? (
        <p className="text-[10px] text-white/30 leading-normal">Save an episode's media as a template, then apply it to another episode once it's dubbed.</p>
      ) : (
        <div className="space-y-1">
          {templates.map((t) => (
            <TemplateRow key={t.id} template={t} projectId={projectId} canApply={!!jobId && !!clipList}
              applying={apply.isPending && apply.variables?.templateId === t.id} onApply={() => applyTemplate(t)} />
          ))}
        </div>
      )}
    </div>
  )
}

function TemplateRow({ template, projectId, canApply, applying, onApply }: {
  template: MediaTemplate; projectId: string; canApply: boolean; applying: boolean; onApply: () => void
}) {
  const rename = useRenameMediaTemplate()
  const remove = useDeleteMediaTemplate()
  const [name, setName] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const commit = () => {
    const next = name?.trim()
    setName(null)
    if (next && next !== template.name) rename.mutate({ templateId: template.id, name: next, projectId })
  }
  return (
    <div className="group flex items-center gap-1 rounded-md px-1.5 py-1 hover:bg-white/[0.04]">
      {name !== null ? (
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') setName(null) }}
          className="flex-1 min-w-0 h-6 px-1.5 rounded bg-ink-950 border border-brand-400/50 text-[11px] text-white focus:outline-none"
        />
      ) : (
        <span className="flex-1 min-w-0 truncate text-[11px] text-white/80" title={template.name}>
          {template.name} <span className="text-white/30">· {template.items.length}</span>
        </span>
      )}
      {confirming ? (
        <>
          <button onClick={() => remove.mutate({ templateId: template.id, projectId })}
            className="h-6 px-1.5 rounded bg-red-500 hover:bg-red-400 text-white text-[10px] font-semibold">Delete</button>
          <button onClick={() => setConfirming(false)} className={cn(iconButton, 'h-6 w-6')} aria-label="Cancel"><X size={11} /></button>
        </>
      ) : (
        <>
          <button onClick={() => setName(template.name)} aria-label={`Rename ${template.name}`}
            className={cn(iconButton, 'h-6 w-6 opacity-0 group-hover:opacity-100')}><Pencil size={10} /></button>
          <button onClick={() => setConfirming(true)} aria-label={`Delete ${template.name}`}
            className={cn(iconButton, 'h-6 w-6 opacity-0 group-hover:opacity-100 hover:text-red-400')}><Trash2 size={10} /></button>
          <Tooltip content={canApply ? 'Add its media to this episode' : 'Open an episode first'} side="bottom">
            <button onClick={onApply} disabled={!canApply || applying}
              className="h-6 px-2 rounded-md bg-brand/15 hover:bg-brand/25 text-brand-200 text-[10px] font-semibold disabled:opacity-40 transition-colors">
              {applying ? <Loader2 size={11} className="animate-spin" /> : 'Apply'}
            </button>
          </Tooltip>
        </>
      )}
    </div>
  )
}
