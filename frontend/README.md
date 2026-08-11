# Dubify Studio — Frontend

React 19 + TypeScript single-window editor for the dubbing studio. One screen does everything: import, transcript editing, timeline, voice generation, and export.

## Scripts

```bash
npm run dev          # http://localhost:5173 — proxies /api, /health, /uploads to 127.0.0.1:8000
npm run lint         # eslint
npm run build        # tsc -b && vite build
npm run tauri dev    # desktop shell (dev: Vite + your own backend)
npm run tauri build  # packaged .app/.dmg (requires the sidecar — see backend/build_sidecar.sh)
```

## Application shell

The editor **is** the app. Every route renders `src/pages/EditorPage.tsx`:

| Route | Meaning |
|---|---|
| `/` | Bare workspace (pick/create a project) |
| `/projects/:projectId` | Project open, no session |
| `/projects/:projectId/jobs/:jobId` | Editing a session |
| anything else | Redirects to `/` |

Routes are keyed by pathname, so **every navigation remounts EditorPage** — player/timeline lifecycles and the per-session store reset depend on that.

```
┌────┬────────────┬─────────────────┬─────────────┐
│ R  │  Dock      │  Video player   │ Transcript  │
│ a  │ (dynamic)  │  + overlay      │ inspector   │
│ i  │            │    canvas       │             │
│ l  ├────────────┴─────────────────┴─────────────┤
│    │              Timeline editor               │
└────┴────────────────────────────────────────────┘
```

- **Rail** (`components/layout/EditorRail.tsx`): icon strip that sets `railTab` in the editor store. Tabs: Projects · Sessions · Speakers · Elements · Voices · Settings. Rail tabs **never navigate** — only rows inside panels do (an auto-navigate effect opens a project's latest session, and tab-switching must not fight it).
- **Dock** (`components/panels/*`): one resizable panel whose content follows `railTab`. Panels are self-contained (own queries, own local state); `PanelShell.tsx` provides the shared header/empty-state chrome. `OverlaysPanel` (in `components/overlays/`) serves the Elements tab.
- The tab auto-derives from context on mount: no project → Projects, project → Sessions, session → Speakers. `railTab` lives **outside** `DEFAULT_STATE` in the store so the per-session `resetEditor()` can't wipe a manual choice.

## State — three layers, kept separate

1. **Server state — TanStack Query only.** `src/api/client.ts` is the single axios layer; `src/hooks/useApi.ts` wraps each endpoint in a query/mutation hook. Job progress is polled via `refetchInterval`. High-frequency mutations (segment drags) write the PATCH response straight into the cache instead of invalidating.
2. **UI-only editor state — `src/store/editorStore.ts` (Zustand).** Playback, zoom, selection, `railTab`, plus `segmentPositions`: optimistic position/text overrides applied ahead of server confirmation.
3. **Undo/redo — `src/store/historyStore.ts`.** Editor state is server-persisted, so each history entry is a pair of inverse **API calls**, not local snapshots. Any new undoable mutation must push an entry via `src/lib/historyHelpers.ts`; the `isApplying` guard stops undo/redo from re-recording itself.

## Key components

```
src/
├── pages/EditorPage.tsx            The whole app: layout, upload flow, export, shortcuts
├── components/
│   ├── layout/EditorRail.tsx       Icon rail
│   ├── panels/                     Dock panels (Projects, Sessions, Speakers, Voices, Settings + shell)
│   ├── overlays/OverlaysPanel.tsx  Elements tab — overlay list + properties
│   ├── video/VideoPlayer.tsx       Player + stem audio + per-segment TTS playback (auto-fit rate)
│   ├── video/OverlayCanvas.tsx     Drag/resize overlays live on the video (fractional coords)
│   │                               Subtitles: bottom-anchored, auto-height, font_size scaled
│   │                               from real video px — mirrors video_overlay.py exactly
│   ├── transcript/TranscriptPanel.tsx  Segment table, text edits, voice assignment, clip inspector
│   └── timeline/TimelineEditor.tsx Lanes, drag/trim/split/snap, waveforms, blade tool
├── store/                          editorStore, historyStore, themeStore
├── hooks/useApi.ts                 All server-state hooks
├── api/client.ts                   All endpoints
├── lib/                            utils, historyHelpers, desktop (Tauri bridge)
└── types/index.ts                  Mirrors backend Pydantic schemas — keep in sync by hand
```

**Keyboard**: Space play/pause · ⌘Z/⌘⇧Z undo/redo · V select · C blade · S/⌘B split · ⌘D duplicate · Delete remove clip/selection. All skip when focus is in an input, textarea, select, or audio element.

**Multi-select**: drag empty track space to box-select (marquee) · ⇧-click toggles a clip · ⇧-drag adds to the selection · Delete removes the whole selection as one undoable step. The timeline selection is the same store the transcript's "N selected → Generate Voice" toolbar acts on.

## Performance patterns (don't undo these)

- The **playhead** moves via direct style writes on refs + a store subscription — playback never re-renders the timeline tree.
- **Clip drags** use `transform` during the gesture and commit to `left` once on pointer-up.
- **Waveforms** decode once per audio file (300 peaks, cached), gated by an IntersectionObserver and a 6-slot concurrency semaphore; zoom only resamples.
- Timeline clips are `memo`-ized with stable callbacks from the parent; `EditorPage` uses per-field store selectors, never a whole-store destructure.

## Desktop notes

- Browser-only vs desktop differences go through `src/lib/desktop.ts`, which degrades to a no-op in the browser (e.g. `chooseVideoSavePath` opens a native Save dialog in Tauri; the browser gets an in-app destination prompt instead).
- Adding a Tauri plugin needs **three** edits: `src-tauri/Cargo.toml`, `.plugin(...)` in `src-tauri/src/lib.rs`, and a permission entry in `src-tauri/capabilities/default.json` — missing the last one fails at runtime only.
- The packaged app serves the built SPA from the FastAPI sidecar on one origin; all frontend paths are relative, keep them that way.
