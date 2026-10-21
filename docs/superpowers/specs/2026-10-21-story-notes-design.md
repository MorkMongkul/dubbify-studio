# Story notes, "this line" and chat history for the Assistant — design

Date: 2026-10-21 · Status: approved in chat (mockups shown in the visual companion; the user chose Story notes in a rail tab, the Assistant knowing the line being edited, chat history per project, and **no** ✨ suggestion at the line)

## Why

- Each new episode is translated knowing nothing about the earlier ones. The translation prompt (`translator.py`) asks Gemini to infer every speaker's gender and how characters address each other from that episode's lines alone, so Khmer names, genders and forms of address (បង / អូន / លោក …) can drift between episodes.
- The user wants to give a project its context — usually 2–3 paragraphs about the story, characters and tone, but optional — and wants the AI to learn from episodes that are already dubbed (their translation is right), reading the first ones together and then each new finished episode, so it gets better episode by episode.
- The Assistant can only find a line the user quotes. "Line 14", "this line" and "this episode" don't work: lines have no numbers, the message carries only text, and the Assistant knows speakers only as `SPEAKER_00`.
- The chat is one endless thread per project, and each reply only sees the last 60 messages (`CHAT_HISTORY_LIMIT`), so the start of a long chat is quietly forgotten.

## Goals

1. **Story notes, per project.** Every project has notes (empty at first, existing projects too):
   - *Your intro* — free text written by the user, optional.
   - *Characters* — Chinese name, the Khmer name used, and a short "about" line (man/woman, age, role, how they address others and are addressed, how they speak), optionally linked to a speaker tag.
   - *Words to keep* — Chinese term → Khmer used, with an optional note.
   - *Story so far* — a short summary per learned episode.
   - *Tone* — one or two sentences about the drama's register and style.
2. **Learning from finished episodes.** The user marks an episode **finished** (only they know its translation is right). The AI then reads every finished episode it hasn't learned yet — the first time typically 1–5 together, after that one at a time — and adds what it learned. It runs in the background; its progress shows in the Story panel only.
3. **Your edits win.** Every character, word, episode summary and the tone records who wrote it (`you` or `ai`). Learning may add new items and update its own, but never changes or removes anything the user wrote or corrected — enforced in code when merging, not left to the model.
4. **Where the notes are used:** the automatic translation of every new episode (Stage 2), "Translate missing lines", subtitle-based translation, and the Assistant (system instruction). The Assistant can still search exact past lines as today.
5. **Where the notes live:** a **Story** tab in the left rail (book icon, before Assistant), a dock panel next to the transcript. The New project window gets an optional *About this drama* box that becomes the intro.
6. **"This line".** The line whose text the user's cursor was last in is the Assistant's "this line". It shows as a tag above the chat box (episode, time, start of the Chinese line, × to remove) and goes with each message, so "make this less formal" works. It follows the text cursor, not the mouse (the transcript's `activeSegmentId` follows hover). The sent message keeps the tag, so a reopened chat still knows which line was meant.
7. **Chat history, per project.** **New chat** and a **History** list (title from the chat's first message, date, message count; open, delete). Each chat sends only its own messages to Gemini. The current single chat of each project becomes its first saved chat; nothing is lost.

## Non-goals

- Any ✨ suggestion box at the line (both mockup options) — the user left it out.
- Chats or notes shared across projects (history option B).
- Detecting "finished" automatically, or "unlearning" when an episode is unmarked (the notes stay; the user can edit them).
- Line numbers in the transcript.

## Anti-goals (a failure even if it works)

- The AI changes or removes something the user wrote or corrected.
- One project's notes reach another project's translation or chat.
- Learning, or a learning failure, blocks or breaks translation, the Assistant or anything else. Without a Gemini key, marking finished still works and the panel says learning needs a key.
- The translation prompt grows without bound as episodes are learned.
- A second spinner for the same thing in another panel (one place per status).

## Decisions

- **Storage.** One JSON document per project in `projects.story_notes` (nullable; missing = empty notes): intro, tone (+ who wrote it), characters, words, episode summaries (by job id, with who wrote each), and the job ids already learned. `jobs.finished_at` (nullable datetime) marks finished episodes. New columns go into an alembic migration **and** `init_db`'s `add_missing_columns` lists (installed apps never run alembic).
- **Model.** Learning uses the Assistant's model (`gemini_models` purpose `chat`) with structured JSON output, like the ASR does. Each learning request carries the current notes (with who wrote what) and the episodes' lines in order: speaker tag, the speaker's display name when set, Chinese, Khmer.
- **Merging** is a pure, tested function: characters match by Chinese name (or speaker tag), words by Chinese term; `you` items are never touched; `ai` items may be updated; new items are added as `ai`; a summary is written for each learned episode unless the user wrote one; tone only while it's `ai`'s.
- **Bounds.** At most 60 characters, 150 words, 400 characters per summary, intro capped at 4,000 characters. The translation prompt gets intro, tone, characters, words and the summaries of up to 5 earlier episodes; the Assistant gets all summaries.
- **One learning run per project at a time**, tracked in memory (a restart ends it; finished-but-unlearned episodes are picked up by the next run or a *Try again*). The Story panel polls the project's learning state while it runs.
- **Chat threads.** A `chat_threads` table (project, title, timestamps) and `chat_messages.thread_id`. A thread is created by its first message (New chat only clears the view, so no empty threads). Messages from before threads existed are put into one thread per project the first time that project's threads are listed. Proposed edits stay per project, above whichever chat is open.

## Left to the builder

- Exact wording of panel labels and empty states, within the app's "less text" rule (short titles, explanations in tooltips).
- How inline editing of a character/word row looks, matching the dock's existing rows.
- The learning prompt's wording.
