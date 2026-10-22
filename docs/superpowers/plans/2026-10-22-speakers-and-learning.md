# Speakers, learning from corrections, transcription — plan (2026-10-22)

Goal: fewer hand fixes per episode. The user corrects every line's speaker and
most translations by hand; their labels and Khmer in the ForbiddenMountain
project (5 episodes) are the answer key. Approved in chat ("go on and complete
all and report back"); step 1 (translation model + who is speaking) is done:
511b1e6, 84825b0. Findings so far: memory `speaker-translation-test-2026-10-22`.

Rules: decide by measurement on the user's episodes (copies in the scratchpad,
deleted afterwards, never their database); TDD for product code; no new UI
pattern without a reason (the user reviews screenshots in the report); nothing
pushed or released without asking.

## Tasks

1. Speaker scoring harness (scratch, not product): `asr.label_speech` on each
   episode's corrected line spans, with references cut only from the OTHER
   episodes' corrected lines; score = lines of known characters labelled right,
   same/different agreement within the episode, speaker count.
2. Better labelling, kept only if the score improves: more reference voices
   (cap 8), several clips per character, names + gender in the reference
   labels, Story-notes names as spelling hints, Gemini's gender/age per new
   voice in the answer.
3. "Choose for me" by Gemini's gender/age instead of pitch (pitch called 5 of 6
   speakers wrong).
4. Merge two speakers (route + Speakers panel action), undoable.
5. Voice-match suggestions (sherpa-onnx + CAM++) — only if voiceprints separate
   the user's corrected speakers well enough; else recorded as not viable.
6. Learn from corrections: keep the AI's first translation per line; retrieve
   the most similar corrected lines in the project as examples in the prompt;
   scored leave-one-episode-out against the user's Khmer.
7. Burned-in subtitles as a transcription/translation hint, if feasible within
   the size budget.
8. Whole-branch review (fresh reviewer), one fix pass, docs, report, ask about
   releasing.
