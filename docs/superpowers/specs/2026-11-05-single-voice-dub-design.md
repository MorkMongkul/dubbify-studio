# Single-voice dub — design

2026-11-05. Owner: the Movelo Studio team (Chinese → Khmer). Status: agreed in conversation, waiting for the owner's
approval of this document.

## Why

Creators who post many dubbed dramas and movies (the "MMO" way of working) don't cast characters at all: one
text-to-speech voice reads every line of the whole video, and a full movie is dubbed in one sitting. Movelo today is
built for careful, character-by-character dubbing (speaker detection, "Who is this?", casting VoxCPM2 voices), which
is the slow part for this kind of work. The owner wants a second way of working inside Movelo for that volume work.

Success: a team member adds an episode or a whole movie, checks the Khmer once, clicks one button, and gets a finished
dubbed video — every line spoken by one Edge TTS voice, the actors' own voices muted, the music kept — with no speaker
work at all. A full movie should finish in well under an hour on the owner's M1 Air (8 GB) once the text is approved.

## Goals

1. A project can be a **single-voice dub** project, chosen when the project is created. Every episode in it follows
   that choice; a normal project is unchanged.
2. **No speaker work** in such a project: no speaker grouping, no character recognition, no "Who is this?", no casting,
   no Speakers panel work needed. Lines have no speaker (or one shared narrator speaker — builder's choice, see below).
3. **Every line is translated** — it is real dubbing of what the actors say, at the moment they say it, not narration
   or a summary.
4. **Translation is a free, natural adaptation**, not word-for-word: understandable spoken Khmer, casual and even
   funny where it fits, short enough to fit the actor's line. It must not sound awkward (ឆ្គង). Story notes (names,
   words to keep) still apply.
5. **One voice for every line**: Microsoft Edge TTS, Khmer voices `km-KH-PisethNeural` (male) and
   `km-KH-SreymomNeural` (female), chosen per project, with a speaking speed per project.
6. Because one voice reads everyone, lines are written to suit that one voice: they avoid man/woman words
   (បាទ / ចាស and gendered pronouns) where the language allows, and otherwise use the voice's own (Option A in the
   conversation — the owner said the exact wording doesn't matter, only that it's understandable).
7. **Default flow — stop to check the text**: after upload, Movelo separates, transcribes (on the Mac) and translates,
   then stops with the Khmer lines in the transcript panel. The team skims and fixes them, then clicks
   **Generate voices & export**, which voices every line and exports the finished video.
8. **Optional — fully automatic**: a per-project switch that goes straight from upload to the finished video. The
   editor stays available afterwards to fix a line, voice it again and export again.
9. **Episodes and full movies**: a project holds episodes as today (a series, episode by episode, Story notes carrying
   across); a full movie is a project with one long episode (1½–2 hours). Both must work on the owner's 8 GB M1 Air.
10. The actors' voices are **muted** (as Movelo's export does today); the music & effects stem stays.

## Non-goals

- Narration, story recaps or shortened videos (the owner considered and rejected narration on 2026-11-05).
- Several voices, voice per character, VoxCPM2 in this mode (could come later; not now).
- Changing how normal (character) projects work.
- Lip sync, subtitles.

## Anti-goals (failure even if it "works")

- A project where the team still has to touch speakers or casting.
- Khmer that reads like a literal translation.
- A full movie that freezes or crashes the app on an 8 GB Mac (memory), in the pipeline or when it's opened.
- Movelo silently paying for something the owner didn't choose (Gemini credits ran out on 2026-10-29; see
  `memory: gemini-cost-local-asr-0.5.6`).

## Decided in conversation (owner)

- Edge TTS, not VoxCPM2, for this mode (fast, free, light). No light local Khmer model is more natural (checked
  2026-11-05: Meta MMS Khmer is robotic and CC-BY-NC; VoxCPM2 is the most natural but heavy).
- Full-length videos, not recaps. Actors' voices muted.
- Default stops after translation for a check; fully automatic is a per-project switch.
- Translation: free, understandable, casual/funny is fine.

## Constraints and facts the builder needs

- **A separate pipeline (owner, 2026-11-05): don't touch or change Movelo's current features or pipeline.** Single-voice
  dubbing lives in its own modules (backend and frontend) and its own routes; normal projects must behave exactly as
  they do in 0.5.6 — same code paths, same tests passing unchanged. Shared code is reused only as it is (separation,
  `local_asr`'s Mac transcription, translator's Gemini plumbing, the export's mixing, the editor); where this mode
  needs a different rule (translation style, what counts as checked Khmer, long-video handling) it gets its own
  function, never a changed existing one. The only touch points in existing code are the ones needed to route a
  single-voice project to its pipeline (e.g. at upload and Analyze) and to show its own UI.

- **Edge TTS** is Microsoft Edge's read-aloud service, reached through the `edge-tts` Python package (7.3.1,
  **LGPL-3.0** — allowed: the PyInstaller one-folder build keeps it a replaceable module, and it must be credited in
  the release notes like FFmpeg and FriBiDi). It needs no key, but it is unofficial: Microsoft can change or block it
  at any time, and its terms for commercial use are unclear. The app must say so plainly where the mode is chosen, and
  a failure must show a clear message ("Edge's voice service didn't answer — try again later"), never silence or a
  Gemini voice. It sends only the Khmer text to Microsoft.
- Edge TTS returns MP3 and takes a speaking rate: only the project's chosen speed is used. **A voice is kept exactly as
  generated** — no automatic fitting or speeding up (owner, 2026-11-05: "I can apply [speed] if it feels slow"); a voice
  longer than its line simply runs on and, as today, moves to another track rather than in time. Many lines (a movie
  has ~1,500) must be voiced with bounded concurrency and retry/backoff; the service throttles bursts.
- **Transcription** uses the Mac path of 0.5.6 (`local_asr`), without speaker grouping, man/woman or recognition (the
  child can skip the voice-kind and print work). Gemini transcribes only where it does today (model not downloaded,
  etc.), and then its speaker labels are ignored.
- **Translation** stays Gemini (`translator.py`), with a new style for these projects: the existing prompt's "keep
  roughly the same length / never add words" rules give way to free adaptation, no WHO IS SPEAKING block, no
  per-speaker politeness, and lines written for the one voice. Story notes and the team's earlier checked lines
  (translation memory) still go with it. The free Google Translate fallback stays as today.
- **Long videos on 8 GB**: today separation reads a whole episode's sound into memory (≈4 GB for 2 hours: input plus
  both stems as float32), and the editor's preview decodes both whole stems into memory (`usePreviewAudio`, ≈1.3 GB
  each for 2 hours), and the stem waveforms decode whole stems too (`useStemPeaks`). Each must work on a 2-hour movie
  on an 8 GB Mac: separation streams through the file in pieces, and the preview/waveforms must not hold whole
  2-hour stems in memory. The transcript panel and timeline must stay usable with ~1,500 lines.
- **Disk**: Movelo copies every upload into its data folder (`source.mp4`); for a 2–4 GB movie that doubles disk use.
  Using the file in place is preferred for long videos, but a file the user moves or deletes afterwards must give a
  clear message, not a broken episode.
- **Columns** on existing tables need both an alembic migration and an `add_missing_columns` entry (CLAUDE.md).
  API shape changes follow models → schemas → route → `types/index.ts` → `client.ts` → `useApi.ts`.
- Mac only; transcription on the Mac needs macOS 13.4+ (else Gemini). Intel Macs must work too, more slowly.

## Left to the builder

- Whether lines in these projects have no speaker or one hidden "narrator" speaker, as long as no speaker UI appears.
- How the project-type choice and its settings look (follow the existing project creation and Settings patterns; the
  owner likes to see two options for UI changes — `memory: ui-mockups-feedback`).
- The exact wording of the translation prompt; it should be measured on a few of the owner's episodes before release
  (a few cents of Gemini; ask the owner first — credits).
- How the preview handles long stems (streaming, windows, or media elements for long videos), and how the transcript
  panel handles ~1,500 lines (virtualised list or similar).
- Concurrency and retry numbers for Edge TTS.
- Whether "Generate voices & export" asks where to save the file (as Export does today) or saves next to the source.

## Calls made after the builder check (Claude's, for the owner to check)

- **No Gemini audio without a click.** A single-voice project never sends a video's sound to Gemini by itself: if
  transcription can't run on the Mac (model still downloading, macOS below 13.4, not Chinese), the episode stops with a
  message saying why, and the Analyze button then transcribes with Gemini on purpose. Translation (text only, a few
  cents an episode) starts by itself after upload in this mode — it's the step the default flow stops after.
- **Failed voices never export a video with holes.** Edge TTS lines are retried with backoff; lines that still fail
  leave the episode with "N lines have no voice" and **Generate missing voices** (like Translate missing lines), and
  fully automatic mode then stops before exporting, with that message.
- **Unchecked Khmer doesn't teach.** In single-voice projects a line counts as checked Khmer for translation memory and
  Story notes learning only when someone edited or approved it, or its episode is marked finished — being voiced
  doesn't count (in fully automatic mode every line is voiced unread).
- **Long-video work is for single-voice projects.** Streaming separation, a preview and waveforms that don't hold
  whole stems, a long transcript panel, and using the movie file in place apply to single-voice projects; normal
  projects keep today's engine (their episodes are short), so its WebKit-tuned preview isn't reworked.
- **Line length:** Gemini is told each line's length and asked to fit it (free adaptation can, unlike the faithful
  translation measured before). The voice is then kept as generated (owner's decision above); a line whose voice runs
  long is marked in the text check so the team can shorten the Khmer or speed it up themselves.

## Open risks

- Edge TTS can disappear or change without notice; there is no official fallback for Khmer in this mode (Azure Speech
  has the same voices with a key, as a possible later backup).
- Free adaptation trades accuracy for naturalness by design; names and key facts must still be right (Story notes).
