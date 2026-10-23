# Voices that fit, timing that follows the speech, loudness that follows the original — plan (2026-10-23)

Goal: dub a 1–3 minute short-drama episode in 10–15 minutes, by removing the
fixes the team makes by hand. Approved in chat (2026-10-23): build these three
and ship them in 0.2.8 with the CPU fix (8c14a58); the voice fallback chain
stays as it is (no change when the Khmer voice server is offline).

Measured on the user's 6 ForbiddenMountain episodes (the user's final timings,
Khmer and voices as the answer key):
- 83% of Khmer voices are longer than their line (median 1.19×, max 1.9×).
- The app's detected spans are ~0.9 s off at start and end; 48% of lines over
  1 s longer than the user's final line.
- Dub loudness is near-constant (-22..-19 dBFS) while the original dialogue
  spans -32..-20 dBFS; dub minus original per line -0.6..+11.7 dB.

Rules: decide by measurement on the user's episodes (read-only; copies in the
scratchpad, deleted after); TDD for product code; preview and export must stay
in lockstep; no new UI pattern without need (the user reviews screenshots);
nothing released before the final review.

## Tasks

1. Timing scoring harness (scratch): the app's segments for each episode
   (Gemini transcription of the episode, as Analyze does) vs the user's final
   lines — start/end error, extra length; and voice-length prediction (Khmer
   characters per second from the team's voiced lines) vs line slots.
2. Lines follow the speech: snap each new line's start/end to where the voice
   starts and stops in the separated vocals; split multi-speaker clips at the
   quietest point near the proportional split. Keep only if the score improves.
3. Voices that fit: translation gets each line's spoken length and a budget;
   after a voice is made, it may use the silence after its line (never
   overlapping the next line) and speed up at most ~1.2×; preview and export
   agree. Measure predicted fit and chrF against the team's Khmer.
4. Loudness follows the original: each voice clip is set to the loudness of
   the original line it replaces (voiced frames of the separated vocals), with
   a ceiling against clipping; fixed target when separation was unavailable.
   Preview and export both play the adjusted clip.
5. Whole-change review (fresh reviewer), one fix pass, docs, release 0.2.8
   (version bump, PR, tag, CI, verify-release, check-upgrade 0.2.7 → 0.2.8,
   publish, merge).
