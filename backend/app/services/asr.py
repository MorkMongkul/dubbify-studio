"""
app/services/asr.py
ASR + voice-based speaker labeling — no separate diarization model.

1. Timing comes from ffmpeg's silencedetect (local, free, deterministic).
   Gemini's own timestamps are NOT used: measured on a real 170 s scene they
   drifted by tens of seconds and ran past the end of the audio.
2. Clips are sent to Gemini a whole scene at a time (a "window" of up to
   ASR_WINDOW_MAX_CLIPS clips) with a "Clip k (d s):" text marker before each
   one. Without markers Gemini treated the clips as one continuous stream and
   shifted text onto neighbouring clips — e.g. a 40-character line on a 0.9 s
   clip that held no speech at all. The duration lets it check how much text
   can fit. Output is structured JSON, so a "[no speech]" line can no longer
   be misread as speaker "N" saying "o speech]".
3. One request both transcribes and labels speakers. On the same scene this
   gave the right roles where a separate speaker-only pass did not: the model
   uses the conversation (who is addressed by name, who answers) as well as
   the voice.
4. A clip may contain several turns — fast exchanges often have pauses shorter
   than the silence threshold — so it is split between the speakers by
   character share instead of forcing one label onto two people.
5. Identity across windows and episodes: each request carries short reference
   clips of voices already identified (R1, R2, ...) — from earlier windows and
   from the project's named/voiced speakers — and Gemini answers with those IDs
   when it hears the same person. New voices get labels not yet taken in the
   project, so episode 2's first voice never silently inherits episode 1's
   SPEAKER_00 name and assigned voice.

This is an LLM listening, not a speaker-embedding model: treat the labels as a
strong starting point the user corrects in the UI, not ground truth.
"""
import asyncio
import base64
import json
import logging
import os
import random
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import httpx

from app.core.config import settings
from app.services import gemini_models
from app.services.diarizer import DiarizedSegment, get_audio_duration
from app.core.binaries import ffmpeg

logger = logging.getLogger(__name__)

DEFAULT_SPEAKER = "SPEAKER_00"

# Silence-detection tuning, chosen to split on sentence-level pauses without
# fragmenting mid-sentence breaths. (A finer split was tried: very short clips
# lose context and transcription accuracy drops sharply.)
SILENCE_NOISE_DB = "-30dB"
SILENCE_MIN_DURATION = 0.4    # seconds of quiet to count as a gap
MIN_SPEECH_DURATION = 0.3     # drop shorter blips (clicks/breaths)
SEGMENT_PADDING = 0.15        # pad each side so words aren't clipped
MERGE_GAP_THRESHOLD = 0.6     # merge neighbours separated by a shorter gap...
MAX_MERGED_DURATION = 12.0    # ...unless the merge would exceed this

ASR_WINDOW_MAX_CLIPS = 40        # clips per request — a whole short scene
ASR_WINDOW_MAX_SECONDS = 300.0   # audio per request: keeps the base64 payload well under Gemini's ~20 MB limit
ASR_MIN_SPLIT_CLIPS = 4          # a failed window at least this big is retried as two halves
ASR_MAX_REFERENCES = 8           # known voices offered per request
REFERENCE_MIN_SECONDS = 1.0      # reference clips must be long enough to carry a voice...
REFERENCE_MAX_SECONDS = 8.0      # ...and short enough to stay cheap
ASR_CALL_TIMEOUT = 180.0         # wall clock per attempt; a 29-clip scene took ~100 s
ASR_MAX_ATTEMPTS = 3             # per request, for 429/5xx/timeouts
ASR_CIRCUIT_BREAKER_FAILURES = 2  # consecutive failed requests before giving up on the rest
ASR_SLICE_CONCURRENCY = 8
# Far more text than a clip can hold means Gemini looped (a real 90 s episode
# once came back with 1,543 characters, ~200 normally): such a clip is asked
# for again. The prompt asks for 3–7 characters per second; this is generous.
MAX_CHARS_PER_SECOND = 20

GEMINI_ASR_THINKING_LEVEL = "low"   # the level the windowed design was validated with
# Gemini 3's thinking tokens share maxOutputTokens with the answer and scale
# with the amount of audio; too small silently truncates (finishReason=MAX_TOKENS).
GEMINI_ASR_MAX_OUTPUT_TOKENS = 24576

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_NO_SPEECH_MARKERS = {"[no speech]", "no speech", "(no speech)", "[无语音]", "[無語音]"}
_LANGUAGE_NAMES = {
    "zh": "Chinese", "en": "English", "km": "Khmer", "th": "Thai", "vi": "Vietnamese",
    "ja": "Japanese", "ko": "Korean", "id": "Indonesian", "ms": "Malay", "fr": "French",
    "es": "Spanish", "de": "German", "ru": "Russian", "hi": "Hindi", "ar": "Arabic",
}

_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "clip": {"type": "INTEGER"},
            "no_speech": {"type": "BOOLEAN"},
            "turns": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "speaker": {"type": "STRING"},
                        "text": {"type": "STRING"},
                        "voice": {"type": "STRING", "enum": ["man", "woman", "boy", "girl"]},
                    },
                    "required": ["speaker", "text"],
                },
            },
        },
        "required": ["clip", "no_speech", "turns"],
    },
}

_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")
_SPEECH_CHAR_RE = re.compile(r"\w")

# Indirections the tests replace; production code always goes through them.
_sleep = asyncio.sleep


def _new_http_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


class GeminiRequestRejected(RuntimeError):
    """A 4xx other than 429: bad key, unknown model, malformed request — retrying can't help."""


@dataclass(frozen=True)
class SpeakerReference:
    """
    A known voice: its global label, a short clip of it and what is said in it,
    plus who it is ("a man, 20s") and other lines that character said. On a
    real project the right character went from 36–42% of lines to ~47% with
    these, 55% together with clips from the original sound: young men's voices
    sound alike, what they say tells them apart.
    """
    label: str
    clip_path: str
    text: str
    duration: float
    about: str = ""
    said: Tuple[str, ...] = ()


# ── Silence detection → speech spans ──────────────────────────────────────────

def _parse_silencedetect_log(log: str) -> List[Tuple[float, float]]:
    """Parse ffmpeg's silencedetect stderr into a list of (start, end) silences."""
    silences: List[Tuple[float, float]] = []
    start = None
    for line in log.splitlines():
        m = _SILENCE_START_RE.search(line)
        if m:
            start = float(m.group(1))
            continue
        m = _SILENCE_END_RE.search(line)
        if m and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    return silences


def _invert_to_speech_spans(
    silences: List[Tuple[float, float]], duration: float
) -> List[Tuple[float, float]]:
    """Silence spans → speech spans across [0, duration]."""
    speech: List[Tuple[float, float]] = []
    cursor = 0.0
    for s, e in silences:
        if s > cursor:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        speech.append((cursor, duration))
    return [(s, e) for s, e in speech if e - s >= MIN_SPEECH_DURATION]


def _merge_and_pad(
    speech: List[Tuple[float, float]], duration: float
) -> List[Tuple[float, float]]:
    """Merge neighbours across short gaps (capped), then pad each span."""
    if not speech:
        return []

    merged: List[Tuple[float, float]] = [speech[0]]
    for s, e in speech[1:]:
        ps, pe = merged[-1]
        if s - pe <= MERGE_GAP_THRESHOLD and e - ps <= MAX_MERGED_DURATION:
            merged[-1] = (ps, e)
        else:
            merged.append((s, e))

    padded = []
    for i, (s, e) in enumerate(merged):
        lo = 0.0 if i == 0 else merged[i - 1][1]
        hi = duration if i == len(merged) - 1 else merged[i + 1][0]
        padded.append((max(lo, s - SEGMENT_PADDING), min(hi, e + SEGMENT_PADDING)))
    return padded


async def _detect_speech_segments(audio_path: str) -> List[Tuple[float, float]]:
    """Silence-based VAD via ffmpeg. Returns merged+padded (start, end) speech spans."""
    duration = get_audio_duration(audio_path)
    if duration <= 0:
        return []

    cmd = [
        ffmpeg(), "-i", audio_path,
        "-af", f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN_DURATION}",
        "-f", "null", "-",
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()

    silences = _parse_silencedetect_log(stderr.decode(errors="ignore"))
    speech = _invert_to_speech_spans(silences, duration)
    spans = _merge_and_pad(speech, duration)

    # A totally silent detector run (e.g. noisy vocals track defeating the
    # threshold) still needs *something* to transcribe — fall back to the
    # whole clip as one span rather than producing zero segments.
    return spans or [(0.0, duration)]


async def _slice_chunk(audio_path: str, start: float, end: float, out_path: str) -> None:
    # -ss BEFORE -i seeks in the input instead of decoding from the start of
    # the file for every clip — measured 1.37 s vs 0.05 s per clip 40 minutes
    # into an episode. Accurate for the WAV stems and for the mp4/mkv/webm video
    # clips are also cut from (pipeline_common.original_sound_for).
    # FLAC (lossless) instead of WAV: a whole-scene request of WAV clips was
    # ~7 MB of base64 and hit upload timeouts on a slow connection.
    cmd = [
        ffmpeg(), "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-t", f"{max(0.05, end - start):.3f}",
        "-i", audio_path,
        "-ar", "16000", "-ac", "1", "-c:a", "flac",
        out_path,
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg slice failed: {stderr.decode(errors='ignore')[:200]}")


# ── Windows and requests ──────────────────────────────────────────────────────

def _plan_windows(
    spans: Sequence[Tuple[float, float]],
    max_clips: Optional[int] = None,
    max_seconds: Optional[float] = None,
) -> List[List[int]]:
    """
    Split clip indices into consecutive windows of at most max_clips clips and
    max_seconds of audio. When a window has to end early, it ends at the
    widest pause in its last quarter, so a conversation isn't cut between two
    requests mid-exchange (speaker identity is most reliable within one request).
    """
    max_clips = max_clips or ASR_WINDOW_MAX_CLIPS
    max_seconds = max_seconds or ASR_WINDOW_MAX_SECONDS
    n = len(spans)
    windows: List[List[int]] = []
    start = 0
    while start < n:
        end = start
        seconds = 0.0
        while end < n and end - start < max_clips:
            clip_seconds = spans[end][1] - spans[end][0]
            if end > start and seconds + clip_seconds > max_seconds:
                break
            seconds += clip_seconds
            end += 1
        if end < n:
            cut_from = start + max(1, ((end - start) * 3) // 4)
            if cut_from < end:
                end = max(range(cut_from, end + 1),
                          key=lambda i: (spans[i][0] - spans[i - 1][1], i))
        windows.append(list(range(start, end)))
        start = end
    return windows


def _audio_part(path: str) -> dict:
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    mime = "audio/flac" if path.endswith(".flac") else "audio/wav"
    return {"inlineData": {"mimeType": mime, "data": data}}


def _window_prompt(n_clips: int, has_refs: bool, language: str) -> str:
    name = _LANGUAGE_NAMES.get(language, language)
    script = (
        "in SIMPLIFIED Chinese characters only (简体字, never traditional/繁體)"
        if language == "zh" else f"in {name}"
    )
    refs_intro = (
        "Before them are reference clips of voices already identified, each labelled with "
        "an ID (R1, R2, ...) and its transcript — and, when known, who it is and other lines "
        "that character said; use those to recognise the character by what they say too.\n" if has_refs else ""
    )
    refs_rule = (
        "If the voice is one of the reference voices, use that exact ID (e.g. R2). " if has_refs else ""
    )
    return (
        f"Below are {n_clips} consecutive audio clips cut, in order, from the dialogue track "
        f"of one scene of a {name} video. Each clip is labelled with its number and exact duration.\n"
        f"{refs_intro}"
        "For EACH numbered clip:\n"
        f"- Transcribe ONLY the speech inside that clip, verbatim, {script}, with natural "
        "punctuation. The text must fit the clip's duration (about 3-7 characters or 2-4 words "
        "per second); never move words into a neighbouring clip.\n"
        "- Split the clip into turns: one turn per continuous stretch by one speaker, in order. "
        "Most clips have one turn; use several only when the voice actually changes inside the clip.\n"
        f"- speaker: {refs_rule}For a voice not heard before, use a new capital letter "
        "(A, B, C, ...) and keep using that letter for the same voice in later clips. Decide by "
        "voice (pitch, timbre) and use the conversation (who is addressed by name, who answers) "
        "as supporting evidence.\n"
        "- voice: who the voice sounds like — a man, woman, boy or girl — judged from the whole "
        "voice and the conversation, not from pitch alone (excited or shouting men can speak as "
        "high as women).\n"
        "- Transcribe spoken dialogue only — never the lyrics of a song playing in the background.\n"
        "- If a clip has no intelligible speech (silence, music, breathing, noise), set "
        "no_speech=true and turns=[].\n"
        "Return one JSON object per numbered clip (none for the reference clips)."
    )


def _build_window_parts(
    clips: Sequence[Tuple[str, float]],
    refs: Sequence[Tuple[str, SpeakerReference]],
    language: str = "zh",
) -> List[dict]:
    """Gemini request parts: instructions, then labelled reference voices, then labelled clips."""
    parts: List[dict] = [{"text": _window_prompt(len(clips), bool(refs), language)}]
    for rid, ref in refs:
        about = f" — {ref.about}" if ref.about else ""
        parts.append({"text": f"Reference voice {rid} ({ref.duration:.1f} s){about}: 「{ref.text}」"})
        parts.append(_audio_part(ref.clip_path))
        if ref.said:
            parts.append({"text": f"{rid} also said, in other episodes: " + " ".join(f"「{line}」" for line in ref.said)})
    for k, (path, duration) in enumerate(clips, 1):
        parts.append({"text": f"Clip {k} ({duration:.1f} s):"})
        parts.append(_audio_part(path))
    return parts


def _retry_after_seconds(resp: httpx.Response) -> Optional[float]:
    """Server-suggested wait: the Retry-After header, or Gemini's RetryInfo.retryDelay ("17s")."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return min(60.0, float(header))
        except ValueError:
            pass
    try:
        for detail in resp.json().get("error", {}).get("details", []):
            delay = str(detail.get("retryDelay", ""))
            if delay.endswith("s"):
                return min(60.0, float(delay[:-1]))
    except (ValueError, AttributeError):
        pass
    return None


async def _post_gemini(payload: dict) -> dict:
    """
    POST with a wall-clock timeout and backoff for 429/5xx/network errors.
    A retired model is replaced (gemini_models) and retried at once — that
    doesn't use up an attempt.
    """
    headers = {"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY}
    last_error: Exception = RuntimeError("Gemini ASR request was not attempted")
    model = gemini_models.current_model("asr")
    attempt = 0
    while attempt < ASR_MAX_ATTEMPTS:
        attempt += 1
        retry_after: Optional[float] = None
        try:
            async with _new_http_client(httpx.Timeout(ASR_CALL_TIMEOUT, connect=15.0)) as client:
                resp = await asyncio.wait_for(
                    client.post(gemini_models.endpoint(model), json=payload, headers=headers),
                    ASR_CALL_TIMEOUT + 10,
                )
        except (httpx.TransportError, asyncio.TimeoutError) as e:
            last_error = RuntimeError(f"Gemini ASR request failed: {type(e).__name__}")
        else:
            if resp.status_code == 200:
                return resp.json()
            message = f"Gemini ASR error {resp.status_code}: {resp.text[:300]}"
            if gemini_models.is_model_unavailable(resp.status_code, resp.text):
                replacement = await gemini_models.switch_model("asr", model, resp.text)
                if replacement:
                    model = replacement
                    attempt -= 1
                    continue
            if resp.status_code not in _RETRYABLE_STATUS:
                raise GeminiRequestRejected(message)
            last_error = RuntimeError(message)
            retry_after = _retry_after_seconds(resp)
        if attempt < ASR_MAX_ATTEMPTS:
            delay = retry_after if retry_after is not None else min(30.0, 2.0 ** attempt) + random.uniform(0, 1)
            logger.warning(f"{last_error} — retrying in {delay:.1f}s (attempt {attempt}/{ASR_MAX_ATTEMPTS})")
            await _sleep(delay)
    raise last_error


async def _call_window(parts: List[dict]) -> list:
    """One structured-output request; returns the parsed JSON rows."""
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": GEMINI_ASR_MAX_OUTPUT_TOKENS,
            "thinkingConfig": {"thinkingLevel": GEMINI_ASR_THINKING_LEVEL},
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
        },
    }
    data = await _post_gemini(payload)
    candidate = (data.get("candidates") or [{}])[0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        raise RuntimeError("Gemini ASR output was cut off (MAX_TOKENS)")
    text = "".join(
        p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought")
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini ASR returned invalid JSON ({candidate.get('finishReason')}): {e}") from e


# ── Response handling ─────────────────────────────────────────────────────────

VOICES = ("man", "woman", "boy", "girl")


def _clip_turns(rows: object, n_clips: int) -> Dict[int, List[Tuple[str, str, Optional[str]]]]:
    """Gemini's valid rows → {clip number: [(speaker token, text, voice or None), ...]}; skipped clips absent."""
    clips: Dict[int, List[Tuple[str, str, Optional[str]]]] = {}
    if not isinstance(rows, list):
        return clips
    for row in rows:
        if not isinstance(row, dict):
            continue
        k = row.get("clip")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= n_clips or k in clips:
            continue
        turns: List[Tuple[str, str, Optional[str]]] = []
        if not row.get("no_speech"):
            for turn in row.get("turns") or []:
                if not isinstance(turn, dict):
                    continue
                text = str(turn.get("text") or "").strip()
                if not text or text.lower() in _NO_SPEECH_MARKERS:
                    continue
                speaker = str(turn.get("speaker") or "").strip().upper() or "?"
                voice = str(turn.get("voice") or "").strip().lower()
                turns.append((speaker, text, voice if voice in VOICES else None))
        clips[k] = turns
    return clips


def _parse_window_rows(rows: object, n_clips: int) -> Dict[int, Optional[List[Tuple[str, str]]]]:
    """
    Validate Gemini's rows → {clip number: [(speaker token, text), ...]}, or None
    for a clip without speech. Clips the model skipped are simply absent.
    """
    return {k: [(speaker, text) for speaker, text, _ in turns] or None
            for k, turns in _clip_turns(rows, n_clips).items()}


def _parse_window_voices(rows: object, n_clips: int) -> Dict[int, List[Optional[str]]]:
    """Who each kept turn's voice sounds like (VOICES or None), in the order of _parse_window_rows' turns."""
    return {k: [voice for _, _, voice in turns] for k, turns in _clip_turns(rows, n_clips).items() if turns}


def heard_voice(votes: Optional[Counter]) -> Optional[str]:
    """The voice Gemini heard most often for one speaker, or None."""
    return votes.most_common(1)[0][0] if votes else None


class _LabelAllocator:
    """Hands out global SPEAKER_XX labels, skipping any already used in the project."""

    def __init__(self, taken: Iterable[str] = ()):
        self._used: Set[str] = set(taken)
        self._next = 0

    def new_label(self) -> str:
        while f"SPEAKER_{self._next:02d}" in self._used:
            self._next += 1
        label = f"SPEAKER_{self._next:02d}"
        self._used.add(label)
        return label


def _resolve_speakers(
    turns_by_clip: Dict[int, Optional[List[Tuple[str, str]]]],
    ref_ids: Dict[str, str],
    allocator: _LabelAllocator,
) -> Dict[int, Optional[List[Tuple[str, str]]]]:
    """Reference IDs → their known labels; every other token → one new label per distinct token."""
    local: Dict[str, str] = {}
    resolved: Dict[int, Optional[List[Tuple[str, str]]]] = {}
    for k in sorted(turns_by_clip):
        turns = turns_by_clip[k]
        if turns is None:
            resolved[k] = None
            continue
        out = []
        for token, text in turns:
            if token in ref_ids:
                label = ref_ids[token]
            else:
                if token not in local:
                    local[token] = allocator.new_label()
                label = local[token]
            out.append((label, text))
        resolved[k] = out
    return resolved


def _speech_chars(text: str) -> int:
    return len(_SPEECH_CHAR_RE.findall(text))


_t2s = None
_t2s_missing_logged = False


def _to_simplified(text: str) -> str:
    """
    Gemini drifts into traditional characters even when told not to — normalise
    deterministically. Without the opencc package (requirements not reinstalled)
    the text is returned as-is rather than failing the whole analysis.
    """
    global _t2s, _t2s_missing_logged
    if _t2s is None:
        try:
            from opencc import OpenCC
        except ImportError:
            if not _t2s_missing_logged:
                logger.warning("opencc-python-reimplemented is not installed — Chinese text is not normalised "
                               "to Simplified. Run: pip install -r requirements.txt")
                _t2s_missing_logged = True
            return text
        _t2s = OpenCC("t2s")
    return _t2s.convert(text)


def _normalise_script(
    turns_by_clip: Dict[int, Optional[List[Tuple[str, str]]]], language: str
) -> Dict[int, Optional[List[Tuple[str, str]]]]:
    if language != "zh":
        return turns_by_clip
    return {
        k: None if turns is None else [(speaker, _to_simplified(text)) for speaker, text in turns]
        for k, turns in turns_by_clip.items()
    }


def _split_turns(
    span: Tuple[float, float], turns: Sequence[Tuple[str, str]]
) -> List[Tuple[float, float, str, str]]:
    """Divide a clip's time span among its turns in proportion to how much each says."""
    start, end = span
    if len(turns) == 1:
        label, text = turns[0]
        return [(start, end, label, text)]
    weights = [max(1, _speech_chars(text)) for _, text in turns]
    total = sum(weights)
    pieces = []
    cursor = start
    for i, ((label, text), weight) in enumerate(zip(turns, weights)):
        stop = end if i == len(turns) - 1 else cursor + (end - start) * weight / total
        pieces.append((round(cursor, 3), round(stop, 3), label, text))
        cursor = stop
    return pieces


def _pick_references(known: Dict[str, SpeakerReference], recent: List[str]) -> List[Tuple[str, SpeakerReference]]:
    """Voices heard most recently in this job first, then project voices; capped."""
    order = [label for label in reversed(recent) if label in known]
    order += [label for label in known if label not in order]
    return [(f"R{n}", known[label]) for n, label in enumerate(order[:ASR_MAX_REFERENCES], 1)]


@dataclass
class _LabelledSpans:
    turns: Dict[int, Optional[List[Tuple[str, str]]]] = field(default_factory=dict)
    failed: Set[int] = field(default_factory=set)
    allocator: _LabelAllocator = field(default_factory=_LabelAllocator)
    voices: Dict[str, Counter] = field(default_factory=dict)   # label → how often each voice was heard


async def _label_windows(
    audio_path: str,
    spans: Sequence[Tuple[float, float]],
    references: Sequence[SpeakerReference] = (),
    taken_labels: Iterable[str] = (),
    language: str = "zh",
    clip_audio: Optional[str] = None,
) -> _LabelledSpans:
    """
    Transcribe + speaker-label every span, window by window, carrying known
    voices forward. Clips are cut from `clip_audio` (the episode's original
    sound) when given, else from `audio_path`.
    """
    result = _LabelledSpans(allocator=_LabelAllocator(set(taken_labels) | {r.label for r in references}))
    if not settings.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY not set — skipping transcription; segments will have no text")
        result.failed = set(range(len(spans)))
        return result

    known: Dict[str, SpeakerReference] = {r.label: r for r in references}
    recent: List[str] = []
    requeued: Set[int] = set()
    consecutive_failures = 0

    with tempfile.TemporaryDirectory(prefix="asr_chunks_") as tmp_dir:
        paths = [os.path.join(tmp_dir, f"chunk_{i:04d}.flac") for i in range(len(spans))]
        sem = asyncio.Semaphore(ASR_SLICE_CONCURRENCY)

        async def _slice(i: int) -> None:
            async with sem:
                await _slice_chunk(clip_audio or audio_path, spans[i][0], spans[i][1], paths[i])

        await asyncio.gather(*(_slice(i) for i in range(len(spans))))

        pending: List[List[int]] = _plan_windows(spans)
        while pending:
            window = pending.pop(0)
            if consecutive_failures >= ASR_CIRCUIT_BREAKER_FAILURES:
                result.failed.update(window)
                continue
            refs = _pick_references(known, recent)
            clips = [(paths[i], spans[i][1] - spans[i][0]) for i in window]
            try:
                rows = await _call_window(_build_window_parts(clips, refs, language))
            except GeminiRequestRejected as e:
                logger.error(f"Gemini ASR rejected the request: {e} — those segments will have no text")
                result.failed.update(window)
                consecutive_failures = ASR_CIRCUIT_BREAKER_FAILURES   # same rejection for every window
                continue
            except Exception as e:
                if len(window) >= ASR_MIN_SPLIT_CLIPS:
                    mid = len(window) // 2
                    logger.warning(f"Gemini ASR window of {len(window)} clips failed ({e}) — retrying as two halves")
                    pending[0:0] = [window[:mid], window[mid:]]
                    continue
                logger.error(f"Gemini ASR failed for {len(window)} clips: {e} — those segments will have no text")
                result.failed.update(window)
                consecutive_failures += 1
                continue

            consecutive_failures = 0
            parsed = _normalise_script(_parse_window_rows(rows, len(window)), language)
            for k, turns in list(parsed.items()):
                seconds = spans[window[k - 1]][1] - spans[window[k - 1]][0]
                if turns and sum(_speech_chars(t) for _, t in turns) > MAX_CHARS_PER_SECOND * seconds + 10:
                    logger.warning(f"Gemini ASR wrote far too much for a {seconds:.1f} s clip — asking again")
                    del parsed[k]
            missing = [window[k - 1] for k in range(1, len(window) + 1) if k not in parsed]
            retry = [i for i in missing if i not in requeued]
            if retry:
                logger.warning(f"Gemini ASR skipped {len(retry)} clip(s) — asking again for just those")
                requeued.update(retry)
                pending.insert(0, retry)
            result.failed.update(i for i in missing if i not in retry)

            resolved = _resolve_speakers(parsed, {rid: ref.label for rid, ref in refs}, result.allocator)
            voices = _parse_window_voices(rows, len(window))
            for k, turns in resolved.items():
                for (label, _), voice in zip(turns or [], voices.get(k, [])):
                    if voice:
                        result.voices.setdefault(label, Counter())[voice] += 1
                i = window[k - 1]
                result.turns[i] = turns
                if not turns:
                    continue
                for label, _ in turns:
                    if label in recent:
                        recent.remove(label)
                    recent.append(label)
                duration = spans[i][1] - spans[i][0]
                if len(turns) == 1 and REFERENCE_MIN_SECONDS <= duration <= REFERENCE_MAX_SECONDS:
                    label, text = turns[0]
                    before = known.get(label)      # a fresher clip, same character: keep who it is
                    known[label] = SpeakerReference(label, paths[i], text, duration,
                                                    about=before.about if before else "",
                                                    said=before.said if before else ())
    return result


# ── Public entry points ───────────────────────────────────────────────────────

def labels_for_spans(
    spans: Sequence[Tuple[float, float]], pieces: Sequence[DiarizedSegment]
) -> Optional[List[str]]:
    """
    A speaker for each fixed (start, end) span — e.g. existing segments —
    taken from a fresh transcription of the same audio: whoever talks longest
    inside the span, else the nearest speaker heard. None if no speech was
    heard at all (nothing trustworthy to relabel with).

    Labelling a fresh, whole-scene transcription and mapping it by overlap is
    far more reliable than labelling arbitrary old spans directly: on a real
    episode whose stored spans held misaligned, mixed speech, direct labelling
    put nearly every segment on the one voice that talked most.
    """
    heard = [p for p in pieces if p.source_text]
    if not heard:
        return None
    labels: List[str] = []
    for start, end in spans:
        overlap: Dict[str, float] = {}
        for p in heard:
            seconds = min(end, p.end_time) - max(start, p.start_time)
            if seconds > 0:
                overlap[p.speaker_label] = overlap.get(p.speaker_label, 0.0) + seconds
        if overlap:
            labels.append(max(overlap, key=overlap.get))
        else:
            nearest = min(heard, key=lambda p: max(p.start_time - end, start - p.end_time))
            labels.append(nearest.speaker_label)
    return labels


async def label_speech(
    audio_path: str,
    spans: List[Tuple[float, float]],
    references: Sequence[SpeakerReference] = (),
    taken_labels: Iterable[str] = (),
    language: str = "zh",
    clip_audio: Optional[str] = None,
) -> Tuple[List[str], List[str], bool]:
    """
    For each (start, end) span, return one speaker label (whoever says the
    most in it) and its transcript. Used where the spans are fixed — subtitle
    cues, or existing segments whose speakers are being re-detected.

    Returns (speaker_labels, texts, used_mock); used_mock=True means at least
    one request failed — those spans get empty text and the previous voice.
    """
    if not spans:
        return [], [], True

    result = await _label_windows(audio_path, spans, references, taken_labels, language, clip_audio)
    labels: List[str] = []
    texts: List[str] = []
    last: Optional[str] = None
    fallback: Optional[str] = None
    for i in range(len(spans)):
        turns = result.turns.get(i)
        if i in result.failed or not turns:
            if last is None and fallback is None:
                fallback = result.allocator.new_label()
            labels.append(last or fallback)
            texts.append("")
            continue
        dominant = max(turns, key=lambda turn: _speech_chars(turn[1]))[0]
        labels.append(dominant)
        texts.append("".join(text for _, text in turns))
        last = dominant
    return labels, texts, bool(result.failed)


async def transcribe_audio(
    audio_path: str,
    references: Sequence[SpeakerReference] = (),
    taken_labels: Iterable[str] = (),
    language: str = "zh",
    clip_audio: Optional[str] = None,
) -> Tuple[List[DiarizedSegment], bool]:
    """
    ASR + voice-based speaker labeling entry point (see module docstring).
    Pauses are found on `audio_path` (the separated vocals: no music to mask
    them); Gemini hears clips cut from `clip_audio` (the original sound) when
    given — measured on a real project, it then told men from women better
    (17 of 19 speakers vs 13 of 18) and transcribed as well or better.

    references: known voices (e.g. the project's named speakers) Gemini may match.
    taken_labels: labels already used in the project — new voices never reuse them.

    Returns (segments, used_mock). Clips without speech are dropped; clips
    whose request failed keep their timing with empty text and used_mock=True.
    """
    spans = await _detect_speech_segments(audio_path)
    if not spans:
        return [], True

    result = await _label_windows(audio_path, spans, references, taken_labels, language, clip_audio)
    segments: List[DiarizedSegment] = []
    last: Optional[str] = None
    fallback: Optional[str] = None
    for i, span in enumerate(spans):
        if i in result.failed:
            if last is None and fallback is None:
                fallback = result.allocator.new_label()
            pieces = [(span[0], span[1], last or fallback, "")]
        elif result.turns.get(i):
            pieces = _split_turns(span, result.turns[i])
        else:
            continue
        for start, end, label, text in pieces:
            segments.append(DiarizedSegment(
                speaker_label=label,
                start_time=round(start, 3),
                end_time=round(end, 3),
                gender=heard_voice(result.voices.get(label)) or "unknown",
                age_group="adult",
                source_text=text,
            ))
            if text:
                last = label

    speaker_count = len({s.speaker_label for s in segments})
    has_text = sum(1 for s in segments if s.source_text)
    logger.info(
        f"ASR: {len(spans)} clips → {len(segments)} segments, {speaker_count} speakers, "
        f"{has_text} with text, {len(result.failed)} failed"
    )
    return segments, bool(result.failed)
