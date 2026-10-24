# Media panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Elements tab with a CapCut-style Media library: per-project images and videos placed on media tracks over the episode, transparent in the preview and the export, with copy/paste and templates applied by hand.

**Architecture:** Three new tables (`media_assets`, `media_clips`, `media_templates`) and a JSON column `jobs.media_track_flags`. Uploads are prepared in the background (thumbnail, an H.264 preview copy with the alpha matte stacked under the colour, the sound as M4A). The editor edits a job's media clips as one list (`PUT` the whole list, like the stem pieces), mirrored by pure rules on both sides; the preview draws every media layer on one WebGL canvas over the player and plays media sound through the preview audio engine; the export overlays the originals with one ffmpeg input per video clip.

**Tech Stack:** FastAPI + SQLAlchemy async (SQLite/Postgres), the bundled LGPL ffmpeg (`app/core/binaries.py`), React + TanStack Query + Zustand, WebGL 1, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-24-media-panel-design.md`

## Global Constraints

- Every ffmpeg/ffprobe call goes through `app/core/binaries.py` `ffmpeg()`/`ffprobe()`; only filters/encoders in the LGPL build (no libx264/libvpx at runtime: H.264 via `h264_videotoolbox`, falling back to `libx264` only where `_has_encoder` says it exists, as the export does).
- New routes take `Depends(get_db, scope="function")`; commit before any long `await` (ffmpeg).
- A new column on an existing table needs an alembic migration **and** an `add_missing_columns` entry; new `*_path` columns go in `data_folder.PATH_COLUMNS`; new child tables are deleted explicitly in project and job deletion.
- Never accept a server file path from the client; media files live under `UPLOAD_DIR/<project>/media/<asset_id>/`.
- Upload limit: `settings.MAX_UPLOAD_SIZE_MB` (500), chunked like `jobs._save_upload`.
- Image clip default 5.0 s; a video clip is at most its source length (from `source_in`); clips cut at the episode's end; volume −20…+10 dB; at most 32 media clips per export.
- In WebKit never seek or change the speed of a *playing* video repeatedly (see CLAUDE.md "Preview playback"); media `<video>` elements stay in the page at opacity 0, never `display:none`.
- Old overlays (subtitle/shape/image rows) are kept in the database but no longer read by the preview, the export or new uploads.
- Keep `backend/app/services/timeline.py` ↔ `frontend/src/lib/*.ts` rules in lockstep where both sides apply them.

## Review Focus

1. The owner's real 55 MB ProRes 4444 file uploaded through the app: its tile becomes ready, and the preview shows the 72% panel blended (not solid) — Task 3 test on a generated ProRes 4444 clip with alpha 184 + Task 12 WebKit check on the real file.
2. Deleting an asset that clips and templates use: its clips leave every episode, templates skip it on apply and say so — Task 4 test.
3. A template applied to a shorter episode, and to an episode that already has media: clips move earlier to end at the end, never overlap, existing clips never move — Task 2 + Task 4 tests.
4. Export with a hidden media track, a muted media track, a video with sound, and 33 clips: hidden not drawn, muted not heard, sound stereo and in time, 33 refused with a message — Task 5 tests.
5. Undo after a drop, a paste, a template apply and an asset delete: one ⌘Z restores the list before (asset delete: not undoable, its steps dropped without an error) — Task 9 + Task 12 checks.

---

### Task 1: Tables, column, cleanup

**Files:** Modify `backend/app/models/models.py`, `backend/app/core/database.py`, `backend/app/core/data_folder.py`, `backend/app/api/routes/projects.py`, `backend/app/api/routes/jobs.py`. Create `backend/alembic/versions/7b8c9d0e1f2a_media.py` (down_revision `6a7b8c9d0e1f`). Test: `backend/app/tests/test_media_tables.py`.

**Interfaces — Produces:**
- `MediaAsset`: `id, project_id (FK projects, index), name, kind ('image'|'video'), path, preview_path, thumb_path, audio_path (nullable), width, height, duration_secs (nullable), has_alpha, alpha_kept, has_audio (bool), last_box (Text JSON nullable), status ('preparing'|'ready'|'failed'), error (nullable), created_at`.
- `MediaClip`: `id, job_id (FK jobs, index), asset_id (FK media_assets), lane_index (int), start_time, end_time, source_in (float 0), x, y, width, height (float fractions), volume_db (float 0), created_at`.
- `MediaTemplate`: `id, project_id (FK, index), name, items (Text JSON), created_at`.
- `Job.media_track_flags = Column(Text, nullable=True)` — `add_missing_columns`: `("jobs", [("media_track_flags", "TEXT")])  # 0.4.0 media tracks`.
- `PATH_COLUMNS` gains `MediaAsset.path, preview_path, thumb_path, audio_path`.

- [ ] Step 1: tests — `test_media_rows_go_with_their_job` (delete job → its media_clips gone, assets stay), `test_media_goes_with_its_project` (delete project → assets, clips, templates gone), `test_old_desktop_db_gets_the_media_column` (a jobs table without the column gains it via `init_db`'s path).
- [ ] Step 2: run `pytest app/tests/test_media_tables.py -v` — FAIL (no models).
- [ ] Step 3: models, migration (idempotent, inspect first, like `5f6a7b8c9d0e_stem_pieces.py`), `add_missing_columns`, `PATH_COLUMNS`, explicit deletes (projects.py next to `StemPiece`; jobs.py next to `StemPiece`).
- [ ] Step 4: tests PASS; full `pytest app/tests -q` green (known flake allowed on re-run).
- [ ] Step 5: commit `feat(media): tables for media assets, clips and templates`.

### Task 2: Media rules (backend, pure)

**Files:** Create `backend/app/services/media.py`. Test: `backend/app/tests/test_media_rules.py`.

**Interfaces — Produces** (clips/assets as plain dicts or objects with the Task 1 fields):
- `IMAGE_SECS = 5.0`, `MAX_EXPORT_CLIPS = 32`, `VOLUME_MIN_DB = -20.0`, `VOLUME_MAX_DB = 10.0`.
- `default_clip(asset, at: float, episode_secs: float, lane: int) -> dict` — length = video `duration_secs` or `IMAGE_SECS`, cut at `episode_secs`; box = `asset.last_box` or `fit_box(asset.width, asset.height, video_w, video_h)`.
- `fit_box(w, h, frame_w, frame_h) -> dict` — contain, centred, fractions.
- `normalize(clips: list[dict], assets: dict[str, asset], episode_secs: float) -> list[dict]` — clamps start ≥ 0, end ≤ episode, length ≥ 0.1, a video's `source_in` ≥ 0 and `source_in + length ≤ duration`, width/height ≥ 0.02, volume in range; raises `ValueError("clips overlap on track N")` when two clips on one lane overlap, `ValueError("unknown media")` for a missing asset.
- `fit_template(items: list[dict], episode_secs: float, existing: list[dict]) -> list[dict]` — a clip past the end moves earlier to end at `episode_secs` (dropped if longer than the episode); keeps its lane when free over its span, else goes to `max(all lanes)+1`; never touches `existing`.
- `prune_flags(flags: dict, clips: list[dict]) -> dict` — like `timeline.prune_flags`, keys `locked|hidden|muted`.

- [ ] Step 1: tests with these exact cases — default video clip 7.0 s at 2.0 → end 9.0; at 85.0 in an 88 s episode → end 88.0; image → 5.0 s; `fit_box(1080,1920, 720,1280)` → `{x:0,y:0,width:1,height:1}`; `fit_box(1000,1000, 720,1280)` → width 1, height 0.5625, y 0.21875; normalize keeps a video at most `duration − source_in`; overlap raises; `fit_template` of a 7 s clip at 100 s into an 88 s episode → 81.0–88.0; onto an episode with a clip on lane 0 over that span → lane 1.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit `feat(media): media clip rules`.

### Task 3: Preparing an upload

**Files:** Create `backend/app/services/media_assets.py`. Test: `backend/app/tests/test_media_assets.py` (skips a case whose test encoder isn't in the local ffmpeg).

**Interfaces — Produces:**
- `AssetFacts` dataclass: `kind, width, height, duration_secs, has_alpha, alpha_kept, has_audio, animated_webp`.
- `async probe_asset(path: Path) -> AssetFacts` — alpha from the video stream's `pix_fmt` (has an `a` plane: `yuva*`, `rgba`, `bgra`, `argb`, `pal8` for GIF with transparency, `ya*`) or a WebM `alpha_mode` tag; `alpha_kept = has_alpha and not (webm or hevc)`; GIF → kind `video`; animated WebP (more than one frame) → `animated_webp=True`.
- `async prepare_asset(asset_id: str, sessionmaker) -> None` — writes `thumb.png` (frame at half the duration, ≤ 320 px long side, keeps alpha), for videos `preview.mp4`: with `alpha_kept` the colour stacked over the alpha matte (`split` → `alphaextract` → both `format=yuv420p` → `vstack`), long side ≤ 1280 per half; opaque: H.264 copy (kept as is when already H.264 yuv420p ≤ 1280), `-movflags +faststart`, `-an`; `audio.m4a` (AAC 128k) when `has_audio`; sets `status='ready'` (or `'failed'` + `error`). No DB session open during ffmpeg.

- [ ] Step 1: tests — a generated ProRes 4444 clip (64×128, white at alpha 0.72, 1 s) → facts `has_alpha and alpha_kept`; after `prepare_asset`, `preview.mp4` is 64×256 and its bottom half's luma at a pixel is 184 ± 8; a PNG with alpha → kind image, has_alpha; an opaque H.264 mp4 with sound → `audio.m4a` exists, preview is plain (height not doubled); a WebM with alpha (if `libvpx-vp9` encodes here) → `alpha_kept False`; an animated WebP (if `libwebp_anim`) → `animated_webp True`.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit `feat(media): prepare uploads — thumbnail, stacked-alpha preview, sound`.

### Task 4: Routes

**Files:** Create `backend/app/api/routes/media.py`; modify `backend/app/schemas/schemas.py`, `backend/app/main.py` (router, prefix `/api/v1`). Test: `backend/app/tests/test_media_routes.py`.

**Interfaces — Produces** (schemas `MediaAssetResponse` with `url`, `preview_url`, `thumb_url`, `audio_url` computed like `OverlayResponse.media_url`; `MediaClipIn/Response`; `MediaClipsUpdate{clips, track_flags?}`; `MediaClipList{clips, track_flags}`; `MediaTemplateResponse{id,name,items,created_at}`):
- `POST /projects/{pid}/media` (multipart `file`) → 201 asset (`status:'preparing'`; animated WebP → 415 "Save it as GIF or ProRes 4444 .mov"; other types → 415); starts `prepare_asset` as a background task.
- `GET /projects/{pid}/media` → assets newest first. `DELETE /media/{id}` → removes its files, its clips in every job, and its items from templates; returns `{removed_clips, episodes}`.
- `GET /jobs/{jid}/media-clips` → `MediaClipList`. `PUT /jobs/{jid}/media-clips` → replaces the job's whole list (`media.normalize`, 422 with the message on ValueError), stores pruned `track_flags` when sent, sets each changed clip's asset `last_box`.
- `POST /projects/{pid}/media-templates {job_id, name}` → 201; `GET /projects/{pid}/media-templates`; `PATCH /media-templates/{tid} {name}`; `DELETE /media-templates/{tid}`; `POST /media-templates/{tid}/apply/{jid}` → `{clips (the job's new list), skipped (int)}` via `media.fit_template`.
- `JobResponse` gains `media_track_flags` and `old_overlays: bool` (the job still has overlay rows).

- [ ] Step 1: tests — upload a PNG → 201 then `GET` lists it ready after prepare; PUT two overlapping clips on lane 0 → 422; PUT a moved clip → asset `last_box` updated; delete an asset used by two jobs and one template → both jobs' lists lose it, `apply` reports `skipped: 1`; template saved from a 120 s job applied to an 88 s job → its 100–107 s clip lands 81–88 s; applied to a job with a clip on lane 0 there → lane 1, the old clip unchanged; animated WebP → 415.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit `feat(media): media library, clips and templates routes`.

### Task 5: Export, and the old overlays leave it

**Files:** Modify `backend/app/api/routes/tts.py` (`_mix_job_task` ~:569–726), `backend/app/services/audio_extractor.py` (`mix_dubbed_audio` :465, `_mix_with_parts` :556–749), `backend/app/api/routes/jobs.py` (drop `apply_default_overlay_template` at :110 and :212). Tests: `backend/app/tests/test_media_export.py`; update `test_export_scale.py` (input bound `DUB_BATCH + 4 + MAX_EXPORT_CLIPS`), `test_overlay_templates.py` (no auto-apply on upload).

**Interfaces — Produces:**
- `mix_dubbed_audio(..., media_layers: list[dict] = (), media_sounds: list[dict] = ())` — a media layer: `{kind:'media_video'|'media_image', path, source_in, length, start, end, x, y, width, height (pixels)}` in draw order (lower tracks first); a media sound: `{input_ref, source_in, length, start, volume_db}` sharing its video input.
- Video: one `-ss source_in -t length -i` per video clip, one `-i` per image asset (`split` when used more than once); each `setpts=PTS-STARTPTS+start/TB`, `scale`, `format=yuva420p`, `overlay=x:y:enable='between(t,start,end)':eof_action=pass`.
- Audio: each sound `[k:a]aformat=…stereo,…,adelay=…:all=1,volume=…` amixed (`normalize=0`) into `[dubbed]` after its `pan=stereo` (:601–605); mono sources made stereo at full level both sides.
- tts.py: no longer queries `Overlay`; builds media layers from the job's clips (skips hidden media tracks for the picture, muted ones for the sound); refuses more than 32 clips with "This episode has N media clips; at most 32 can be exported."

- [ ] Step 1: tests (real ffmpeg, small generated files) — a 64×128 ProRes clip with alpha 0.72 white over a solid (105,136,115) test video: the exported pixel ≈ 0.722·255 + 0.278·base (±6) inside its window, base outside it; a hidden media track → base only; a 1 kHz media sound at −6 dB in [2,3] s present in both channels in that window only; muted → absent; 33 clips → the message; an old shape overlay row → not applied.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS, plus full suite. Step 5: commit `feat(media): the export overlays media and mixes its sound; old overlays leave it`.

### Task 6: Voice tracks lose their hide switch

**Files:** Modify `backend/app/services/timeline.py` (`FLAG_KEYS`, `flags_from`, `free_lane`, `settle`, `export_lines`), `backend/app/schemas/schemas.py` (`TrackFlags` without `hidden`), `frontend/src/lib/trackFlags.ts`, `frontend/src/types/index.ts`, `frontend/src/components/timeline/TimelineEditor.tsx` (no eye on voice tracks). Tests: update `test_track_flags.py`, `tests/trackFlags.test.ts`.

- [ ] Step 1: tests — `flags_from('{"0":{"hidden":true,"locked":true}}') == {0: {locked: True, muted: False}}`; a clip moved by `settle` ignores a stored hidden flag; `exportWarnings` no longer mentions subtitles (muted tracks only, plus muted/hidden media tracks from Task 7's helper).
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS (backend + `npm test`). Step 5: commit `fix(tracks): no hide switch on voice tracks while there are no subtitles`.

### Task 7: Editor rules, types, API

**Files:** Create `frontend/src/lib/media.ts`; modify `frontend/src/types/index.ts`, `frontend/src/api/client.ts`, `frontend/src/hooks/useApi.ts`, `frontend/src/lib/historyHelpers.ts`. Test: `frontend/tests/media.test.ts`.

**Interfaces — Produces:**
- Types `MediaAsset`, `MediaClip`, `MediaTemplate`, `MediaClipList` (mirroring Task 4).
- `lib/media.ts` (pure): `IMAGE_SECS`, `defaultClip(asset, at, episodeSecs, lane, frame)`, `fitBox(...)`, `trimMediaStart(clip, asset, t)`, `trimMediaEnd(clip, asset, t, episodeSecs)` (video within source, never over a lane neighbour), `pasteClips(copied, at, existing)` (keeps relative times and boxes, new ids, top free spot), `duplicateClip(clip, existing)` (right after if free, else lane above), `splitClip(clip, at)`, `mediaRows(clips)`, `drawOrder(clips, flags)` (lanes ascending, hidden skipped). Drops reuse `lib/tracks.planDrop`.
- client `media` (assets/list/upload with `onUploadProgress`/remove, clips get/put, templates list/save/rename/remove/apply); hooks `useMediaAssets(projectId)` (polls while any is preparing), `useUploadMedia`, `useDeleteMedia`, `useMediaClips(jobId)`, `useSetMediaClips` (optimistic, like `useSetStemPieces`), `useMediaTemplates`, `useSaveMediaTemplate`, `useApplyMediaTemplate`, …
- `recordMediaChange(qc, jobId, before: MediaClipList, after: MediaClipList, label)` — undo/redo = PUT the list.

- [ ] Step 1: tests — the same numeric cases as Task 2 for `defaultClip`/`fitBox`; `trimMediaEnd` of a 7 s video can't pass 7 s, an image can reach the episode end; paste at 30 s of two clips 2 s apart keeps 2 s apart, avoids an occupied lane; duplicate right after; split keeps `source_in` continuous.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit `feat(media): editor rules, types and API for media`.

### Task 8: The Media tab

**Files:** Create `frontend/src/components/panels/MediaPanel.tsx`; modify `frontend/src/store/editorStore.ts` (`RailTab` `'elements'` → `'media'`), `frontend/src/components/layout/EditorRail.tsx` (`{ tab: 'media', icon: Clapperboard?/Film, label: 'Media' }`), `frontend/src/pages/EditorPage.tsx` (panel switch; remove `OverlaysPanel`, the logo handlers and `useOverlays`). Remove `frontend/src/components/overlays/OverlaysPanel.tsx` usage (file deleted if nothing else imports it).

- [ ] Step 1: build — Import button + drop zone (images and videos; progress while uploading), tile grid (thumbnail on a dark checker for alpha, duration badge, name, "Added" when in the open episode, "Preparing…" / failure text, the "transparency isn't kept" note), tile `+` (adds at the playhead via `defaultClip` + `useSetMediaClips` + `recordMediaChange`), tile drag (`dataTransfer` type `application/x-movelo-media` = asset id), tile delete with a confirmation naming the episodes; Templates section: save (name), list with Apply (to the open episode, undoable), rename, delete. Icon-first, Movelo look.
- [ ] Step 2: `npm run lint && npx tsc -b`; WebKit screenshot of the tab with two assets.
- [ ] Step 3: commit `feat(media): the Media tab replaces Elements`.

### Task 9: Media tracks on the timeline

**Files:** Create `frontend/src/components/timeline/MediaClipView.tsx`, `frontend/src/components/timeline/MediaTracks.tsx`; modify `frontend/src/components/timeline/TimelineEditor.tsx` (rows above the picture strip; drop target for tiles; keys ⌘C/⌘V/⌘D/⌫/S for a media selection; selection separate from voice clips — new `editorStore.selectedMediaIds`).

- [ ] Step 1: build — rows per media lane (top lane highest), header with kind icon + lock/hide/mute (`media_track_flags`, mute only with sound), clips with thumbnail strip and name, drag in time and between media lanes via `planDrop` (insert line), trims via `trimMediaStart/End` (locked lanes refuse), right-click: Copy, Paste, Duplicate, Split at playhead, Delete; tile drop at the pointer's time/lane; every change = `useSetMediaClips` + `recordMediaChange`.
- [ ] Step 2: `npm run lint && npx tsc -b && npm test`; WebKit: drop a tile, trim, ⌘C/⌘V, ⌘Z restores each.
- [ ] Step 3: commit `feat(media): media tracks on the timeline`.

### Task 10: Media on the picture

**Files:** Create `frontend/src/components/video/MediaCanvas.tsx`, `frontend/src/lib/mediaGl.ts` (WebGL program: colour from the top half, alpha from the bottom half's red channel; images drawn straight); modify `frontend/src/components/video/VideoPlayer.tsx` (remove `OverlayCanvas` and the `overlays` prop; mount `MediaCanvas`), `frontend/src/pages/EditorPage.tsx` (no overlays prop; the export window's old-overlays line from `job.old_overlays`).

- [ ] Step 1: build — one canvas sized to the player's frame, layers per `drawOrder`; a hidden `<video>` per media video clip near the playhead (opacity 0 in the DOM), loaded and paused at `source_in` ~2 s ahead, played on the main video's frame (`requestVideoFrameCallback`) when its clip starts, speed set only while stopped, a drift > 0.3 s fixed once by pause→seek→play, scrub seeks coalesced; paused → exact frame; selection box for the selected media clip (move, corner resize keeping aspect) committing one `useSetMediaClips` change.
- [ ] Step 2: lint/tsc; WebKit: the generated alpha clip over a red-background test page reads ≈ (250, 188, 188) at the panel pixel (as measured for the design).
- [ ] Step 3: commit `feat(media): media drawn over the picture with its transparency`.

### Task 11: Media sound in the preview

**Files:** Modify `frontend/src/hooks/usePreviewAudio.ts` (a media clip with `audio_url` plays like a voice: buffer from `audio_url`, window `source_in`…, level = editor volume × `volume_db`, skipped on muted media tracks), `frontend/src/lib/audioPlan.ts` if a pure helper is needed (`mediaSoundStart(clip, t)` with a test in `tests/audioPlan.test.ts`), `frontend/src/components/video/VideoPlayer.tsx` (pass media clips + flags).

- [ ] Step 1: test `mediaSoundStart` (before / inside / after the clip, like `voiceStart`). Step 2: FAIL. Step 3: implement + wire. Step 4: PASS + lint/tsc. Step 5: commit `feat(media): media sound in the preview`.

### Task 12: Docs and the real file

**Files:** Modify `CLAUDE.md` (Media section; remove/replace the overlays description), `frontend/src/lib/trackFlags.ts` warnings if not done.

- [ ] Step 1: WebKit end-to-end on a copy of the owner's data with the real `Locora_Sponsor_LowerThird_alpha.mov`: upload → ready; drop at 5 s; preview pixel blended; ⌘C/⌘V to 40 s; save template; apply to another episode; export; the exported card pixel ≈ the correct blend.
- [ ] Step 2: full `pytest app/tests -q`, `npm run lint`, `npm test`, `npm run build`.
- [ ] Step 3: commit `docs: the Media panel`.
