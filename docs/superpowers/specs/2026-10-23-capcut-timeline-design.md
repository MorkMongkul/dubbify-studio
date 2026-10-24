# A CapCut-style timeline — design (2026-10-23)

Ships in three versions, in this order: **0.3.0** (clips follow their voice,
CapCut tracks, icon-first look), **0.3.1** (shaping the sound), **0.3.2** (the
episode's own rows). Each version is released and checked on its own before the
next one starts. Approved with the user in chat on 2026-10-23 ("nothing
missing", "Ok go with it").

## Why

The team has dubbed with Movelo for two weeks. Their biggest time sink is the
timeline:

- A clip's length is the original actor's line, not its Khmer voice. The two
  rarely match, and the app hides the difference: the preview
  (`VideoPlayer.tsx` `startTTS`) and the export (`tts.py` `_autofit_rate` →
  `audio_extractor._tempo_chain`) silently speed up a voice that is longer than
  its clip, up to 3.5×, so **shortening a clip makes its voice faster**; and
  **Auto-fit** (`segments.py` `autofit_segment_speeds`) changes a voice's speed
  to fill its clip, so **stretching a clip makes its voice slower**.
- The voice's waveform is stretched across the whole clip
  (`SegmentWaveform width={width}`), so nobody can see where the voice ends.
- A line with no voice yet keeps the actor's timing; short ones cut their text
  off ("លោ", "នៅ…") and can't be read.
- Tracks don't behave like CapCut's: a dropped clip that overlaps another is
  pushed sideways in time, off its line (`TimelineEditor.tsx`
  `resolveDragOverlap`); emptied tracks stay; a group can't change tracks.
- The editor has too much wording for a professional tool.

The user compares everything with CapCut and wants Movelo's timeline to behave
like it, wherever that helps dubbing.

## Goals

1. What you see is what you hear: a clip's length on the timeline is the length
   of the sound it plays, and the preview and the export play exactly the same
   thing. Nothing changes a voice's speed unless the user (or Auto-fit, when
   asked) sets it, and the speed is shown on the clip.
2. Tracks work like CapCut's.
3. The timeline looks and reads like a professional editor: icons, not words.
4. (0.3.1) Fades, a volume line, music that dips under voices, sound while
   scrubbing.
5. (0.3.2) The music and the original voices can be cut and shaped too; pieces
   of the original voices can go into the export; a picture strip helps find
   scenes.

## Non-goals

Cutting the video itself; a cover image (later, in the export window);
recording your own voice; markers, copy/paste, track lock, taller tracks (not
picked); transitions, filters, stickers, keyframes, speed curves; the main-track
magnet / ripple delete; removing wording from the rest of the app (the next
project, after this one, in the same icon-first style).

## Anti-goals (a failure even if everything "works")

- A line moving off its timing without the user moving it.
- Any speed change the user can't see on the clip.
- The preview and the export sounding different.
- Existing episodes sounding different after an update without the user doing
  anything (see "Upgrading existing episodes").
- New status text or spinners beyond the one-place rule (CLAUDE.md).

## The chosen look

Option **B, "Movelo look"** (the user clicked it on the mockup page): Movelo's
navy surfaces and coral accent, each clip filled with its speaker's colour, as
today, in CapCut's layout. Mockups:
`.superpowers/brainstorm/8319-1792768860/content/timeline-look.html` (B is the
second) and `audio-tracks.html` (music and original voices in pieces, 0.3.2).

- **Toolbar, icons only** (each with a tooltip giving its name and shortcut):
  left — select tool, undo, redo · split, trim start, trim end, delete ·
  Auto-fit, Tidy. Right — hear-while-dragging (0.3.1), snapping · zoom to fit,
  zoom out, zoom slider, zoom in. No "Timeline" label, no "Split"/"Auto-fit"/
  "Tidy" words. The selection count may stay as a number.
- **Track headers are icons**, no "Track N" text. Music: music icon, volume,
  ducking toggle (0.3.1). Original voices: their icon and the mute toggle.
  Voice tracks: no controls in 0.3.0 (lock and mute weren't picked — my call).
- **Right-click on a clip** opens a menu with icons, names and shortcuts:
  Split (S) · Trim start (Q) · Trim end (W) — New voice · Speed › — Delete (⌫).
  Menus keep their words; that is how CapCut does it too.

## 0.3.0 — the timeline follows the voice

### Clip length

- **A clip with a voice is exactly as long as its voice** (as trimmed, at its
  speed). Its waveform is drawn at true scale.
- **Dragging an edge of a voiced clip cuts sound**, like CapCut: the left edge
  removes sound from the start, the right edge from the end. It never changes
  the speed, and an edge can't go past the voice's own start or end. Q / W do
  the same at the playhead. A trim is kept in the voice's own time, so changing
  the speed later keeps the same words cut.
- **A clip with no voice yet is as long as its Khmer text takes to say**, at the
  pace measured from the team's voiced lines (see "Measurements"). Its length
  follows its text: it is re-estimated whenever the Khmer text changes, and its
  edges can't be dragged (there's no sound to cut; the voice will set the
  length). It can still be moved. Its text wraps onto a second line, and it has
  a dashed border.
- **Pointing at any clip shows its whole line** (tooltip), since a short clip
  can still be too narrow at some zoom levels.
- **Speed is a setting of the clip**: shown on the clip when it isn't 1.0×
  (gauge icon + "1.1×"), changed from the right-click menu (Speed ›) or the
  transcript panel's existing control; the clip's length follows. The voice
  file keeps carrying its speed (`render_clip` already bakes `voice_speed`).
- **Where the actor spoke** is kept per line (new: the line's original span)
  and drawn as a thin bracket under the clip. A line made by analysis gets its
  detected span. A line that exists before the update gets the span it has at
  the upgrade (the team's own timing, often already widened for the Khmer
  voice — the best record there is; re-detecting would cost Gemini calls). A
  line added by hand has none: no bracket, no red stripes of its own. The span
  is a fact about the video, so it **stays put when the clip is moved**. The
  features that today read `start_time/end_time` as the actor's timing switch
  to this span, falling back to the clip where there is none: the pitch that
  picks a voice (`auto_voice.py`), the reference clips for speaker detection
  (`pipeline_common.build_speaker_references`), the loudness a new voice is
  matched to (`voice_level.level_new_voice`), and mapping a fresh
  transcription onto existing lines (`asr.labels_for_spans`). (My call.)
- **Red stripes** mark the part of a voice that runs into a later line where
  the original didn't overlap: the span from `max(next line's start, this
  line's original end)` to `min(this clip's end, next clip's end)`, for any
  line that starts after this one, on any track. Two characters who really
  talk at once in the original are not red.

### Split, duplicate, and text changes (my calls)

- **Splitting a voiced clip** works like CapCut: both halves keep playing
  their own part of the one voice (the first trimmed at the end, the second
  trimmed at the start, with its own copy of the voice files, since voices are
  stored and re-rendered per line). The Khmer and Chinese text are split at the
  phrase break (a space, ។, or punctuation) nearest to the cut's share of the
  line; with no break, the second half gets the same text, as today. The actor's
  span is split at the cut time. One undo step.
- **Splitting a clip with no voice** splits its text the same way; each half's
  length then follows its own text.
- **Duplicate** copies the voice too (its own copy of the files), like CapCut.
- **Changing the Khmer text of a voiced line keeps its voice** and marks the
  clip as voiced from older text (an icon; New voice is in the menu), instead
  of deleting the voice as today (`TranscriptPanel.tsx` sends
  `tts_audio_path: ''`). Deleting it would make the clip jump to its text
  length and throw away a voice the user may have tuned, and fixing a split
  half's text would always cost its voice. This applies to every text change:
  typing, an approved Assistant edit, Translate missing lines.
- A clip with no voice re-estimates its length when its text is saved, not on
  every keystroke.

### Auto-fit (new job)

Auto-fit only **speeds up voices that run into a later line**, just enough to
end before that line starts, and **never above 1.25×** (a natural-sounding cap
— my call; tune it if the team hears otherwise). It never slows a voice down,
never lowers a speed the user set, and leaves anything it can't fix within the
cap red. The 0.2.8 slow-down floor (`AUTOFIT_SLOWEST`) goes away with the old
job.

### Generating a voice

The new clip starts where the line starts and is as long as the voice.
`tts._make_room` (0.2.8's "extend the line into the silence after it") is
replaced by this rule. If the new length overlaps the next clip on the same
track, the clip moves to the nearest track where it fits (see Tracks) — never
in time.

### Tracks (like CapCut)

- Any voice clip, or a selected group, can be dragged onto any voice track.
  Dropping it above the first or below the last track creates a new track
  there; a drop between two tracks (a thin highlight line shows it) inserts a
  track there.
- **A track left empty disappears**; tracks are always numbered without gaps.
  There's no permanent empty track at the bottom any more.
- **A drop that would overlap a clip on the target track never moves the clip
  in time**: it goes onto a new track inserted just below the target.
- **A clip that grows into the next clip on its track, for any reason**
  (a new voice, a slower speed, an edge dragged back out, a longer text, the
  upgrade), moves to the nearest track where it fits, creating one if needed —
  never in time, never overlapping on one track. Growth happens on the server
  too (text edits, batch voice generation, many at once), so the server applies
  this rule and compacts empty tracks after every change; the frontend uses the
  same rules to show a drag before it lands. (My call.)
- Tidy stays (packs clips into the fewest tracks, true overlaps on separate
  ones).
- Every track change is one undo step, including the renumbering it causes.

### Keys

J / K / L: play backward, pause, play forward (repeated L: 2×, then 4×; J
likewise, or stepping back in short jumps where the window can't play video
backward). ← / →: one frame (the video's frame rate, else 1/30 s). ↑ / ↓: the
previous / next clip edge. Q / W: trim the selected clip's start / end at the
playhead. Existing keys stay (V, C, S, ⌘B, ⌘D, Space, ⌘Z / ⇧⌘Z). None fire
while typing.

### Export and preview

One rule decides what a clip plays, used by both: the voice file from the
clip's cut-off start, for exactly the clip's length, at the clip's start time,
at the speed baked into the file. The export's silent `fit_rate` speed-up goes
away. Burned-in subtitles keep following each clip's start and end (they read
`segments.start_time/end_time`), so they now follow the voice. Export must keep
mixing voices in `DUB_BATCH` parts (no input per line).

### Upgrading existing episodes (my call)

Existing episodes must sound the same after the update. A voice the old app
sped up to fit (preview and export both did, when no speed was set and the
voice was >1.05× its clip) gets that speed as its visible setting — up to the
2.0× a clip render allows; anything beyond that is longer than before and shows
red. A voice shorter than its clip played at 1× before; its clip now ends where
its voice ends. Lines with no voice take their text length; their old span
becomes "where the actor spoke". The conversion must not block opening the
editor, and until a line is converted the preview and the export must still
agree.

## 0.3.1 — shaping the sound

- **Fades**: handles on the top corners of a selected clip; dragging inward
  sets a fade-in / fade-out, drawn as a shaded slope. They apply to the clip as
  trimmed, in the preview and the export alike, and are undoable.
- **Volume line** on each voice clip: a horizontal line inside the clip; drag
  it up or down to set the clip's volume (the existing `volume_db`, same range
  as the transcript panel's control), with the value shown while dragging; the
  preview follows live, the change is saved on release, undoable.
- **Music dips under voices** (ducking): wherever a voice clip plays, the music
  and effects go down by a set amount with short ramps. A toggle on the music
  track header. On for episodes analysed from 0.3.1 on, off for older ones so
  their exports don't change (my call); the amount and ramp are the builder's
  choice (start around −8 dB and 0.2 s, and listen).
- **Hear while dragging**: with the headphones toggle on (default), dragging
  the playhead plays short snippets of what the export sounds like at that
  point.

## 0.3.2 — the episode's own rows

- **Music and effects in pieces**: split (blade, S, ⌘B), trim, delete, a volume
  line and fades per piece. Pieces are **locked to the picture**: they can't be
  dragged sideways (a door slam must stay on the door), and deleting one leaves
  silence there. A trimmed edge can be dragged back out to restore sound, up to
  the next piece. The track header's volume (today's `bgm_volume_db`) still
  applies to the whole track, and ducking applies on top.
- **Original voices in pieces**, with the same tools. They stay out of the
  export as today, except pieces the user switches on (an icon on the piece and
  in its right-click menu), to keep the actor's real laugh, scream, crying or
  song. A switched-on piece is drawn in gold.
- **Picture strip**: frames from the video in a row above the tracks, like
  CapCut's video track, to find scenes. For looking only: the video can't be
  cut. Frames are made with the bundled LGPL ffmpeg and cached per episode;
  making them never blocks the editor.
- Export: still one input per stem (no input per piece); pieces' volumes and
  fades in one filter chain per stem.

## Constraints (from CLAUDE.md, apply to every version)

- A new column on an existing table needs both an alembic migration and an
  `add_missing_columns` entry (installed apps never run alembic).
- API shape changes go models → schemas → route → `types/index.ts` →
  `client.ts` → `useApi.ts`.
- Every editor change the user makes is undoable through `historyHelpers.ts`.
- Server state in TanStack Query, UI state in `editorStore`, undo in
  `historyStore` — kept separate.
- Only filters in the LGPL ffmpeg build (`afade`, `volume`, `atempo`, `atrim`
  are there; check anything else with `vendor/ffmpeg/<target>/ffmpeg -filters`).
- Routes use `Depends(get_db, scope="function")`; no DB session open across a
  long await.
- Never send a client-supplied server path.
- Pure logic the frontend needs (lane placement, red spans, the text-length
  estimate, Auto-fit's targets) lives in modules free of runtime imports so
  `npm test` covers it; backend behaviour is covered by pytest, including an
  export test per version proving the export plays what the preview plays.

## Measurements

- **Speaking pace for the text estimate — measured 2026-10-23**, read-only on
  the user's app data: the 18 voices made since 0.2.8 (silence trimmed,
  `level_gain_db` set, speed 1.0) last **0.065 s per character of the Khmer
  text** (Unicode code points, punctuation included), median error 0.11 s (9%);
  syllable counts did worse (0.25 s, 17%). Older, untrimmed voices carry the
  ~0.6 s of leading silence (fit 0.61 s + 0.061 s/char), as expected. Today's
  `SECS_PER_CHAR = 0.045` (`TimelineEditor.tsx`) is ~30% short. 18 lines is a
  small sample: re-measure once more voices exist, and record the number in
  CLAUDE.md.

## Left to the builder

Column names and types; how the upgrade conversion runs (per episode when it
is opened, or in the background at start-up); how trims are stored (an offset
into the voice); the snippet length and mixing for hear-while-dragging; the
ducking amount and ramps; the picture strip's frame spacing and storage; the
exact icons (lucide, already in the app).
