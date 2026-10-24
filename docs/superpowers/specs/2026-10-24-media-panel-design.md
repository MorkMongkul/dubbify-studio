# Media panel (CapCut-style) — design

Date: 2026-10-24 · Status: for the owner's approval

## Why

The team has to put an animated, transparent sponsor lower-third (Locora, `Locora_Sponsor_LowerThird_alpha.mov`) over episodes that carry a sponsor. Today's Elements tab can't: it only places a still image, a subtitle style and a blur/cover box, none of them on the timeline. The owner wants Elements to become **Media**, working like CapCut's Media: upload images and videos, put them on the timeline over the episode, see them in the preview, have them in the export — with transparent video staying transparent, never on a black background.

## The owner's decisions (2026-10-24)

- The Elements tab becomes **Media** and works like CapCut's Media.
- The subtitle style, the blur/cover box and the logo are **removed** — from the panel, the preview and the export — until a later discussion. The owner confirmed the team no longer delivers burned-in subtitles or the blur box. *My call:* their database rows are kept (not deleted), so nothing is lost if they return; exporting an episode that still has them says so first ("This episode's subtitles, cover box or logo are no longer in exports").
- The media library is **per project**: every episode of a project shares it.
- A media clip's **own sound is kept**, with a volume line and a mute switch, like CapCut.

## Measured facts this design rests on (the owner's own files, the app's own ffmpeg)

- The sponsor files are **ProRes 4444 with real transparency** (`yuva444p12le`, 1080×1920, 30 fps, 7 s, ~55 MB, no sound). The card's white panel is 72% opaque (alpha 184).
- **Export:** the bundled LGPL ffmpeg reads ProRes 4444 transparency. Overlaid on a real episode, the card's pixel came out (214, 221, 214) against the correct blend (213, 222, 216); 10 s took 1.3 s.
- **Preview:** a `<video>` on screen draws partly transparent areas as opaque (the panel came out solid white), while a frame drawn into a canvas blends correctly ((250, 188, 188) against (250, 191, 191)). So media is drawn on a canvas.
- The bundled ffmpeg can't read transparency from WebM (needs libvpx, not in the build) or HEVC-with-alpha files, and can't read animated WebP at all. It reads PNG, still WebP, GIF and QuickTime Animation (`qtrle`) transparency.
- Using one video asset twice through ffmpeg's `split` held 1.14 GB (a 7 s clip at 5 s and at 100 s of a 120 s episode) against ~300 MB with one input per clip.

## Goals

1. **Media tab** (replaces Elements; icon-first): Import button and drag-and-drop; a grid of tiles with thumbnail, duration (videos), name and an "Added" mark when used in the open episode; delete asks first, naming the episodes that use it, and removes its clips (can't be undone).
2. **Formats:** images PNG/JPG/still WebP; videos MP4/MOV/M4V/MKV/WebM and animated GIF (a GIF is a video that plays once). Transparency kept for ProRes 4444, QuickTime Animation, PNG, WebP, GIF. A WebM or HEVC-with-alpha upload says on its tile that its transparency isn't kept ("Export it as ProRes 4444 .mov"); an animated WebP is refused ("Save it as GIF or ProRes 4444 .mov"). Upload limit: the existing 500 MB.
3. **On the timeline:** media clips on their own tracks **above the picture strip** (CapCut puts overlays above the main track). A higher track is drawn over a lower one, so **a media clip never changes track by itself**: drag in time and between media tracks (a drop onto a busy track or between tracks makes a new track, like the voice tracks), trims stop at the neighbouring clip, a duplicate goes right after the original when there's room, else onto a new track above; Tidy leaves media tracks alone; empty media tracks vanish. Add by dragging a tile onto the timeline (at the pointer) or the tile's + (at the playhead, on the top free spot).
4. **Clip edits:** trim either edge, split, duplicate, delete, select several — each one undo step. A video clip starts as its full length, an image as 5 s, both cut at the episode's end. **Lengths, like CapCut:** an image stretches to any length (drag its right edge); a video can be shortened but never stretched past its own end (the owner asked; the sponsor card's own animation ends at 6.5 s of 7, so a longer card needs a longer file from the designer). **Copy, like CapCut:** ⌘C copies the selected media clips, ⌘V pastes them at the playhead on the top free spot (never over another clip), keeping their box, length and volume; right-click › Duplicate puts a copy right after. Media and voice selections are separate (selecting one clears the other).
5. **On the picture:** a selected media clip shows a box on the player — drag to move, corners to resize keeping its shape. A new clip of an asset takes the box that asset was last placed at; the first time, it fits the frame (contain, centred) — the sponsor card, the episodes' own shape, fills it.
6. **Track headers** for media tracks: kind icon (image/video), lock, hide (picture not drawn in preview or export), mute (sound only; tracks with sound).
7. **Sound:** a video's own sound plays in the preview through the preview audio engine (like a voice: its window, volume, track mute) and is mixed into the export; volume line −20…+10 dB like the voice clips, no fades this version; it isn't dipped by the music ducking; hear-while-dragging keeps playing voices and music only this version.
8. **Removing the old Elements:** the subtitle, blur box and logo overlays are no longer drawn in the preview nor burned into exports; templates no longer apply to new episodes; their panel UI goes. The voice tracks' **hide** switch is removed while there are no subtitles (it only hid subtitles) — from the header and from the stored switches, since a leftover hidden flag still steers where the server places clips (`free_lane`/`settle`); lock and mute stay. The export warning stops mentioning subtitles and lists muted or hidden media tracks.

## Non-goals (this version)

Text, stickers, effects, filters, transitions, keyframes/animation of position, opacity control, rotation, cropping, speed of media clips, chroma key (the owner's greenscreen copies aren't needed), WebM/HEVC-alpha transparency, animated WebP, a library shared across projects, adding a clip to every episode at once, a cover image, exporting an episode with no voices.

## How it works

### Library (backend)
- New table **`media_assets`**: `id, project_id, name, kind ('image'|'video'), path (the original), preview_path, thumb_path, audio_path (nullable), width, height, duration_secs (videos), has_alpha, alpha_kept, has_audio, last_box (JSON, nullable), status ('preparing'|'ready'|'failed'), created_at`. Files under `uploads/<project>/media/<asset_id>/`; paths added to `data_folder.PATH_COLUMNS`; deleted with the project.
- `POST /projects/{id}/media` (multipart, the video upload's chunked save and 500 MB limit), `GET /projects/{id}/media`, `DELETE /media/{id}`.
- After upload, in the background: ffprobe → kind, size, duration, alpha (pix_fmt has an alpha plane; WebM's alpha tag), sound; a thumbnail; for videos a **preview copy** made with the bundled ffmpeg — **H.264, the colour picture stacked over its alpha matte** (one double-height frame; opaque videos get a plain H.264 copy, kept as is when already one), long side ≤ 1280; and the sound as an M4A/WAV for the preview engine. H.264 decodes on every Mac the app supports (macOS 11+), so there's no Mac where transparency falls back to black — unlike HEVC-with-alpha, which older Intel Macs may not encode. The tile shows "Preparing…" until done.

### Clips (backend)
- New table **`media_clips`**: `id, job_id, asset_id, lane_index, start_time, end_time, source_in, x, y, width, height (fractions of the frame), volume_db, created_at`; deleted with the job/project/asset. Media track switches in a new `jobs.media_track_flags` (JSON keyed by media lane; alembic + `add_missing_columns`), separate from the voice tracks' `track_flags`.
- Routes: list, create, a batch layout PUT (moves/trims/splits in one request), update box/volume (also saves the asset's `last_box`), delete, and the media track flags.

### Preview (editor)
- **One canvas over the player draws every media layer in track order** (images and videos alike, so layers interleave correctly), with WebGL: a video's colour from the top half of its preview frame, its alpha from the bottom half.
- Each media video has a hidden `<video>` (in the page at opacity 0, not `display:none`, so WebKit keeps decoding it), loaded and paused at its source position ~2 s before its clip begins, started on the main video's frame (`requestVideoFrameCallback`) when the clip begins; its speed is set only while stopped (a speed change stops a playing WebKit video); a drift over ~0.3 s is fixed once by pause → seek → play, never by repeated seeks or speed nudges (the loop 0.3.1 measured); seeks while scrubbing are coalesced; paused, it shows the exact frame.
- Sound: the preview audio engine schedules media sound like a voice.
- Selection/move/resize on the player, like the old image overlays' interaction (`OverlayCanvas` pattern), committed on release as one undo step.

### Export
- **One ffmpeg input per video clip** (`-ss source_in -t length -i asset`) and **one per image asset** (a single frame that `overlay` repeats; several uses `split` it); each `setpts` to its start, `scale` to its box, `format=yuva420p`, `overlay … enable=between(t,start,end)`, lower tracks first. Media sound comes from the clip's own input (`[k:a]`), `atrim`/`adelay`/`volume`, made stereo at full level on both sides, and mixed into the dub **after** its mono→stereo pan, before the music (so every branch of the mix gets it).
- At most **32 media clips per export** (the 256-open-files rule: today's run has ≤ 52 inputs); more shows a clear message before exporting. `test_export_scale.py`'s input bound grows by that.

## Left to the builder
Exact tile and track-header visuals (Movelo look, CapCut's Media panel as the model), the preview copy's bitrate and thumbnail size, the WebGL shader details, where the delete confirmation lives, keyboard shortcuts for media clips (follow the voice clips'), test layout.
