# Characters recognised by voice and face — design

Date: 2026-10-25 · Owner approved the direction and the transcript layout (option A) in conversation.

## Why

The owner dubs short Chinese dramas episode after episode with the same actors. Today Movelo can't keep an actor's identity across episodes or even within one, so every new episode needs voices re-assigned:

- Gemini decides who speaks (one request per scene, with a reference clip per known speaker). On the owner's checked episodes it names the right character for only **~40–46%** of lines (measured below), and its grouping inside an episode is mixed: only about half of its labels hold a single person.
- One actor ends up under several labels. In the owner's project ForbiddenMountain, SPEAKER_01 and SPEAKER_03 are both the female lead (both cast with the voice Kim Sreymom), SPEAKER_04 and SPEAKER_02 both the male lead (Ly Visal), SPEAKER_09/_07/_05 one character (Heng Dara).
- **60 of 213 lines (28%)** had their Khmer voice changed by hand, one line at a time.

Goal: Movelo knows each **character** (the actor) by their own voice and their talking face, from the lines already checked, so a new episode arrives with most lines on the right character and the right Khmer voice — and points at the ones it isn't sure about.

## Measured (ForbiddenMountain, episodes 1–10, which the owner checked; ep11 not yet)

Truth = the Khmer voice each line ended up with (the owner's correction; in their project one voice = one actor). "As it happens" = episode N only knows episodes 1..N−1. Scripts and data lived in the session scratchpad (`spk/`), reproducible with `scripts/measure-characters.py` (see Acceptance).

| Signal | Answers on | Right when it answers |
|---|---|---|
| Voice profile — mean ERes2NetV2 voice print of a character's earlier lines, cosine | ~99% of lines | **67%** (CAM++: 52–58%) |
| Speaking face — Light-ASD picks the face whose lips match the sound; its SFace print vs each character's mean | ~53% of lines (speaker on screen) | **80–81%** |
| Gemini with one reference per character (today's mechanism, `asr.transcribe_audio` + `labels_for_spans`) | ~94% | 39–46% (mostly a *wrong known* character) |
| Face on screen without lip check | — | 42–49% (listeners are often on screen) |

Combined:
- Speaking face, else voice: **≈70%** of lines right.
- **"Sure" rule** — face and voice agree, **or** nobody on screen is speaking and the voice's best match leads the second by ≥ 0.10 cosine: sure on **57%** of lines at **83%** right overall; **60% at 90%** on episodes 6–10 (more profile data).
- Adding Gemini as a third vote made it worse (majority of three: 75–80% right on 70%). Gemini's speaker decision is dropped.
- Matching Gemini's label *groups* to characters instead of single lines: ≤ 72% (half its groups are mixed).

Timing ("who speaks *when*") was tested separately and is **out of scope** here (see Non-goals).

## What changes for the user

1. **Characters, not SPEAKER labels.** The Speakers panel lists characters: the actor's face (cut from an episode) and the Khmer voice they're cast with. A character is recognised by the actor's voice and face — never by the Khmer voice — so two characters *may* share a Khmer voice (minor parts) and recasting a character doesn't break recognition. Character names stay editable as today (`display_name`).
2. **After analysis, every line already has a character.** About 6 in 10 are marked sure. The rest show an amber **"Who is this?"** in the transcript with Movelo's two best guesses (face thumbnail, name, and what the guess rests on: "voice + face", "face", "voice"), one click or the keys **1** / **2**, plus **Someone else…** (the existing speaker picker / new-speaker box). The transcript header shows **"N to check"** and **Next to check ▸**. (Layout: option A of `.superpowers/brainstorm/28939-1792911008/content/who-check.html`.)
3. **Every confirmation teaches the profile**, so later episodes need fewer clicks.
4. **Existing projects**: profiles are built from the episodes already checked. **Lines whose Khmer voice was changed one by one become characters** (owner, 2026-10-25: such a change always meant *another actor* was speaking and no label existed for them — e.g. ForbiddenMountain's Tan Sophea, 30 lines; Meng Piseth, 14; Kong Sokunthea, 10). See *Upgrading existing projects*. Labels that look like one character get a one-click **"Same character?"** suggestion (the existing *Same person as…* merge does the work): suggested when two characters share a Khmer voice, or when their voice and face profiles are near each other. **Movelo never merges by itself** — every merge is the user's click, so it stays one undo step and never re-voices lines by surprise. A suggestion also has **Not the same**, which dismisses it for good (it never comes back for that pair).
5. **Picking another character re-translates that line** for the new speaker (Khmer politeness and pronouns depend on who speaks: បាទ for a man, ចាស for a woman, លោក/បង/អូន…) — one Gemini call for that line, with the same WHO IS SPEAKING cast and Story notes as translation — **unless the user has edited that line's Khmer**; one undo step restores the previous Khmer. (My call, offered to the owner, who was unsure; see the calls list.)
6. **Detect speakers** re-checks an episode's lines against the profiles — local, no Gemini call — without touching text, timing, or any line the user already confirmed.

## How it works

### Per line: prints

For every line, from its actor span (`timeline.actor_span`), computed once and stored:

- **Voice print**: ERes2NetV2 (3D-Speaker, `3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx`, 192-d) on the separated vocals (`vocals.wav`), 16 kHz mono, L2-normalised. Lines shorter than 0.3 s get none.
- **Speaking face**: faces detected every frame at 25 fps inside the span (OpenCV YuNet `face_detection_yunet_2023mar.onnx`, score ≥ 0.7, faces ≥ 40 px), followed by box overlap (IoU ≥ 0.3, gaps ≤ 0.09 s); for each face track of ≥ 10 frames, **Light-ASD** (MIT, `pretrain_AVA_CVPR.model`, exported to ONNX at build time) scores frame by frame whether that face is speaking the line's audio (inputs as its demo: face crop padded by 0.40 → 224 → grey centre 112×112; 13 MFCCs at 100 Hz from the same audio, int16 scale). The track with the highest mean score is the speaking face **if its mean > 0**; its print = mean of SFace (`face_recognition_sface_2021dec.onnx`, 128-d) features on every 6th frame, L2-normalised. Otherwise the line has no speaking face (speaker off-screen). Also keep a small face crop of the speaking face (for thumbnails).
- In the spike the demo's box smoothing (median over 13 frames) was skipped to save memory; it measured as above without it.

### Per character: profile

A character's voice profile and face profile = the normalised mean of the prints of its **confirmed** lines. A line counts as confirmed when (same spirit as `pipeline_common.translation_memory`):
- the user picked its character in "Who is this?" or changed its speaker by hand (the character picker), or
- it isn't marked to check (`who_check` is null) and it was voiced (has `tts_audio_path`), approved, or its episode is marked finished.

A line still marked **to check never counts**, even once voiced or in a finished episode — only a pick confirms it — so a mistake can't teach itself. "Voice all" and "Mark finished" say "N lines still to check" first. Changing one line's Khmer voice (the per-line voice picker) is **casting only** from now on (e.g. another voice for a flashback): it doesn't change the line's character and doesn't count as confirming it. Profiles are recomputed from stored prints (cheap: means of a few hundred 192/128-d vectors), never stored as a running sum that a later correction can't undo.

### Per line: the decision

For each line with known characters:
- voice: the character with the highest cosine to the line's voice print, and the margin to the second;
- face: if the line has a speaking face, the character with the highest cosine to its face print.

Assign: face's pick when there is a speaking face, else voice's pick. **Sure** when face and voice agree, or there's no speaking face and the voice margin ≥ 0.10. Otherwise **to check**, with the two best guesses (face's and voice's picks when they differ; else the voice's top two) stored for the transcript chips, each with its basis.

**People not seen before** (no character's voice cosine reaches a floor the builder sets from data, and no face match): such lines keep Gemini's in-episode grouping as new characters (as today, `ensure_speakers`), marked to check.

**Re-analyze** replaces an episode's lines (as today, "start over"), but each new line that overlaps a confirmed old line keeps that line's character (by time overlap, like `asr.labels_for_spans`) and its confirmed state; prints are computed for the new lines.

**First episode of a project** (no profiles): exactly as today — Gemini's labels become characters; the owner casts voices. Prints are still computed so the profiles exist from then on.

### Where it runs

In Stage 2, **after transcription and before translation**: translation uses who is speaking (WHO IS SPEAKING cast: Khmer politeness បាទ/ចាស and pronouns), so it must see the recognised characters. The same step serves **Detect speakers** (prints already stored → only the decision re-runs, plus prints for lines that lack them) and the subtitle pipeline. Gemini keeps transcribing and still returns per-turn labels (used only for unknown people and the first episode) and the heard voice (man/woman/boy/girl, kept for "Choose for me"); its reference clips can be dropped once profiles exist (builder's call: they cost tokens and measured no gain).

Measured cost on an Apple-chip Mac: Light-ASD over 10 episodes ≈ 11.5 min (≈ 70 s per 90–150 s episode, 1.5 GB peak), voice prints seconds. It's a step of its own on the progress ring (`RING_STEPS` in `lib/jobProgress.ts`, `job_clock`), measured on real runs, Intel included.

## Upgrading existing projects

Once per project (at start-up, like `timeline.convert_old_jobs`, marked so it never runs twice):
- Each line whose own Khmer voice differs from its speaker's (`segments.voice_id` set and ≠ `speakers.voice_id`) moves to the character cast with that voice: if one character in the project has it, that one; if several, the one with the most lines; if none, a **new character** cast with that voice (named after nothing — `display_name` empty, as new speakers today). The line's own voice is then cleared (the character carries it). Lines moved this way count as confirmed (they were the owner's hand fix).
- Prints are computed for every line of the project in the background; profiles exist when that ends.
- Then the "Same character?" suggestions appear (e.g. ForbiddenMountain's SPEAKER_01/_03, both Kim Sreymom).
- Nothing else of the owner's work changes (text, timing, voices made).

## Data (types stay in sync: models → schemas → routes → types → client → useApi)

- New table **`line_prints`**: `segment_id` (PK, FK segments), `job_id`, `voice_print` (BLOB, float32 ×192, nullable), `face_print` (BLOB, float32 ×128, nullable), `speaking_score` (float, nullable), `face_crop_path` (nullable), `model_version` (text: lets a model change recompute), `made_at`. Deleted with its segment, job and project (Postgres enforces FKs — follow the stem_pieces pattern). New table → `create_all` makes it on desktop installs.
- **`segments.who_check`** (TEXT JSON, nullable): null = sure or confirmed; else `[{speaker_id, basis}, {speaker_id, basis}]`. Column on an existing table → **alembic migration + `add_missing_columns` entry**.
- **`segments.who_confirmed`** (BOOLEAN, nullable/false) if the builder needs it to tell a user's pick from Movelo's (the confirmed-line rule). Same migration rule.
- **`speakers.face_crop_path`** (TEXT, nullable): the card's thumbnail (best-scoring face crop among its confirmed lines, refreshed when it changes). Same migration rule. Crops live under the job's uploads folder; paths go through `PATH_COLUMNS` (`app/core/data_folder.py`) like other media paths.
- Re-translating one line needs to know whether the user edited its Khmer: keep the machine draft or an edited flag (builder's choice; `Translate missing lines` today only fills empty lines, so nothing records an edit yet). Column rule as above.
- Dismissed "Same character?" pairs are kept per project (a small table or JSON on the project — builder's choice) so they never come back.
- Routes: `POST /jobs/{id}/identify` (Detect speakers' new engine), the existing segment PATCH clears `who_check` and marks the pick confirmed when `speaker_id` changes, `GET /projects/{id}/character-suggestions` (pairs to merge, with the reason), the existing merge/unmerge routes do the merge. Routes keep `Depends(get_db, scope="function")`; no DB session open while models run (SQLite has one writer).

## Constraints

- **Local models ship with the app** (owner approved; ends "cloud-only" for this step only). Fetched at build time from pinned URLs with SHA-256 (not committed: the repo is public and ERes2NetV2 is 69 MB), bundled by `build_sidecar.sh`, both chips, checked by `movelo-backend --self-test` (load each model, run one tiny input). Sizes: ERes2NetV2 69 MB, SFace 37 MB, YuNet 0.2 MB, Light-ASD ~4 MB (ONNX). Runtime: sherpa-onnx (voice prints; already used in the spike) and ONNX Runtime/OpenCV DNN for the rest — the builder picks the smallest set that runs all four; no PyTorch in the app. The app must stay well under 1 GB.
- Licences to verify and credit in the release notes' *Included software*: Light-ASD (MIT), 3D-Speaker models (Apache-2.0, check), OpenCV zoo YuNet/SFace (check each), sherpa-onnx (Apache-2.0), OpenCV. If OpenCV's own video decoding pulls in a non-LGPL FFmpeg, decode frames with the bundled ffmpeg instead (`app/core/binaries.py`).
- Faces never leave the Mac.
- Never change a line the user confirmed (their pick, a hand change, a voiced line) — not by analysis re-runs, not by Detect speakers, not by a merge.
- No automatic merges. "Same character?" suggestions use thresholds the builder derives from ForbiddenMountain after the upgrade (its true characters: one per Khmer voice — 8 actors).
- Intel Macs: the face-and-lips step must take ≤ ~3 min per 90–150 s episode there; if it can't, Intel runs voice only (still better than today: 67% vs ~40–46%), and the release notes say so.
- Undo: a "Who is this?" pick is one undo step (as other segment edits); a merge keeps its existing one-call undo.

## Non-goals

- **Timing ("who speaks when").** Measured: today's detected starts are within 0.2 s of the owner's for ~62% of lines in episodes 7–10; speech-onset models (Silero VAD, pyannote segmentation) and lip onsets didn't fix the re-timed lines, because those are mostly over-long detected stretches (5–11 s, several sentences merged) and wrong-speech assignments, not edge errors. Finer sentence cutting is the next, separate experiment.
- Changing transcription or translation models; changing TTS or the voice library.
- Cloud face recognition; Gemini as a speaker voter.

## Anti-goals (a failure even if it works)

- A character silently split again into duplicates (the thing being fixed).
- A confident wrong assignment with no "check" mark where face and voice disagree.
- Confirmed work overwritten.
- The analysis noticeably slower on the owner's Mac than the ~1 min the step measured, or a crash/hang on Intel.

## Acceptance

- `scripts/measure-characters.py <data-folder-copy> <project>`: runs the **shipped engine** on a read-only copy (`sqlite3 -readonly .backup`, media read in place) after the upgrade, truth = each line's character after the upgrade (= its final Khmer voice in ForbiddenMountain), "as it happens": overall right ≥ 68%, sure-rule coverage ≥ 50% at ≥ 83% right on ForbiddenMountain episodes 1–10 (the owner's checked episodes; ep11 once they finish it). Run it before release; the owner's data never goes into the repo or CI. It takes an optional corrections file (line → character) for answer-key mistakes the owner points out: on 2026-10-25, while practising, the owner heard ep10's line at 1:26.3 (尊上，要不咱们找点乐子？) as **Meng Piseth's** actor, though their saved ep10 still voices it with Ly Visal — Movelo's face guess was right there, so the measured numbers are, if anything, conservative.
- Unit tests for the decision rule (sure/check, margins, unknown people, confirmed lines untouched), the profile rule (confirmed lines only), migrations on an old desktop DB, deletes with segment/job/project.
- End to end in WebKit: a new episode of a project with checked episodes → "N to check", chips, 1/2 keys, "Next to check", undo.

## Calls made for the owner to check

1. No automatic merges — only "Same character?" suggestions (the reviewer showed background merges can't be undone and would re-voice lines).
2. A line still "to check" never teaches a profile, even voiced or in a finished episode; "Voice all" / "Mark finished" warn about open checks.
3. Re-analyze keeps confirmed characters on overlapping new lines.
4. Intel: voice only if the face step can't stay ≤ ~3 min per episode.
5. Picking another character re-translates that line unless its Khmer was edited (owner unsure — recommended).
6. The per-line voice picker becomes casting only; the upgrade turns past one-line voice changes into characters (owner confirmed the meaning).

## Ideas noticed, not in this build

- Where face and voice strongly point at another character on a line the user already confirmed or voiced, ask "Sounds like Meng Piseth?" — it would have caught the ep10 slip above.

## Left to the builder

The runtime stack for the four models; the unknown-person floor and the auto-merge thresholds (from data); how prints are batched/parallelised and their progress-ring estimate; thumbnail choice and size; the exact transcript/Speakers-panel styling within option A; whether Gemini's reference clips are dropped; how existing projects' prints are computed (all at once in the background on first open of a project vs on demand).
