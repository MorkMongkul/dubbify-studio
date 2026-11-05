"""Transcription on this Mac (0.5.6): what each line says, who says it and whether a man or a woman — without Gemini,
which then only translates.

Measured 2026-11-01 on the owner's M1 Air with 10 of their finished episodes, against the team's checked Chinese:
Qwen3-ASR 0.6B (int8, Apache-2.0; k2-fsa's conversion) given the Story notes' names was the best of six local models —
32% of characters different (Gemini 3.6 Flash: 22–45% on the 2 episodes compared; much of either is the team's own
edits), 34 of 65 names right (SenseVoice: 2, FireRed: 12, FunASR-Nano: 18; without the names Qwen3-ASR got 14), 17–21 s
for a ~100 s episode on ~1.9 cores, 1.7 GB, no thermal throttling. Given names, it sometimes writes them all out on a
quiet clip (10 times in 10 episodes): clean_text removes that. Man or woman comes from k2-fsa's Zipformer audio tagger
(AudioSet's "Male/Female speech"; Apache-2.0 — CED, as accurate, is GPL-3.0): 88% of lines, 91% of characters per
episode (Gemini's own call: 5 of 7 characters on that project). Voices are grouped by the same voice prints character
recognition uses (average linkage, GROUP_SIMILARITY): 84% of lines in a group that is mostly one character, 79% of a
character's lines in their main group (Gemini, one episode: 70% / 80%). Recognition then names the characters it
knows, as after Gemini.

The clips are the ones Gemini would hear (asr._detect_speech_segments on the vocals); the text is heard from the
original sound (measured better), the voice kind and print from the vocals. All three models run in a child process
(the packaged backend's own executable with CHILD_FLAG; `python -m` in development): Qwen3-ASR holds 1.7 GB, which
the backend, running all day, gets back when the child ends. The models aren't in the app: they're downloaded once
into the data folder (model_files; ~1 GB). Until then, below macOS 13.4, for a language other than Chinese, with
Settings → AI models → Transcription on Gemini, or when this fails, Gemini transcribes (pipeline.transcribe_episode).
"""
import asyncio
import json
import logging
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.config import settings
from app.services import asr
from app.services.diarizer import DiarizedSegment

logger = logging.getLogger(__name__)


@dataclass
class ModelSpec:
    folder: str
    base_url: str
    files: dict   # name → SHA-256


MODELS = [
    ModelSpec("sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25",
              "https://huggingface.co/csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/resolve/"
              "68818b2313fe77bd06f6a7c5068ff3ef59d02b8a", {
                  "conv_frontend.onnx": "d22dc4423e0940e49884e903d2ea2f7e5567c14fc1aed97e4e26d6b8f208ef9e",
                  "encoder.int8.onnx": "60748d3e6744a57c9c91e1b17424a6c2990567e8adceb0783940c03ed98fa9d9",
                  "decoder.int8.onnx": "4f6885be5959ae26af3089d38ee7972c5fafbeeb1cf8d5e76eab6d8b61ca5771",
                  "tokenizer/merges.txt": "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
                  "tokenizer/tokenizer_config.json": "4942d005604266809309cabc9f4e9cb89ce855d59b14681fdc0e1cc62ea26c4c",
                  "tokenizer/vocab.json": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
              }),
    ModelSpec("sherpa-onnx-zipformer-small-audio-tagging-2024-04-15",
              "https://huggingface.co/k2-fsa/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/resolve/"
              "c24f9d0fdf7d0b8c0c4f9733aa0cac51fda10c95", {
                  "model.int8.onnx": "69304b8a1b96bbe6b7d16c24079f0732c65faf3568a14cb82a8238c709afe76c",
                  "class_labels_indices.csv": "cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429",
              }),
]
ASR, TAGGER = MODELS
MALE, FEMALE = "Male speech, man speaking", "Female speech, woman speaking"
GROUP_SIMILARITY = 0.45   # average voice-print cosine two groups need to be one speaker (0.35–0.65 tried; see above)
MAX_NAMES = 200
CHILD_FLAG = "--transcribe"

_download_lock = asyncio.Lock()
_child_lock = asyncio.Lock()   # one child at a time (each holds ~2 GB)


def model_dir(spec: ModelSpec) -> Path | None:
    if not settings.DUBIFY_DATA_DIR:
        return None
    return Path(settings.DUBIFY_DATA_DIR) / "models" / spec.folder


def model_ready() -> bool:
    from app.services import model_files
    from app.services.characters import models
    return models.runtime_supported() and all(model_files.ready(model_dir(m), m.files) for m in MODELS)


async def ensure_model() -> bool:
    """Downloads what isn't there yet (model_files.download). True when both models are ready."""
    from app.services import model_files
    from app.services.characters import models
    if not settings.DUBIFY_DATA_DIR or not models.runtime_supported():
        return False
    async with _download_lock:
        ok = True
        for spec in MODELS:
            if not model_files.ready(model_dir(spec), spec.files):
                ok = await model_files.download(model_dir(spec), spec.base_url, spec.files,
                                                f"Transcription ({spec.folder})") and ok
        return ok


def use_mac(language: str) -> bool:
    """Whether this analysis transcribes on this Mac."""
    return settings.TRANSCRIPTION == "mac" and (language or "zh").startswith("zh") and model_ready()


# ── text and names ───────────────────────────────────────────────

_REPEATED_MARK = re.compile(r"([。，！？、,.!?…])\1+")


def clean_text(text: str, names: list[str]) -> str:
    """What Qwen3-ASR wrote, in Simplified Chinese, without the names list read back; "" when nothing was said."""
    text = asr._to_simplified(text or "")
    for sep in (" ", ",", "，", "、"):
        echo = sep.join(names)
        if names and echo:
            text = text.replace(echo, "")
    text = _REPEATED_MARK.sub(r"\1", text).strip(" ，,、")
    text = re.sub(r"^[。！？.!?]+", "", text)
    return text if asr._speech_chars(text) else ""


def names_from_notes(notes) -> list[str]:
    """The Chinese names and words of the Story notes, for Qwen3-ASR to listen for (unnamed SPEAKER_XX left out)."""
    if not isinstance(notes, dict):
        return []
    out: list[str] = []
    for kind in ("characters", "words"):
        for item in notes.get(kind) or []:
            zh = (item.get("zh") or "").strip() if isinstance(item, dict) else ""
            if zh and not zh.upper().startswith("SPEAKER_") and zh not in out:
                out.append(zh)
    return out[:MAX_NAMES]


# ── voices ───────────────────────────────────────────────────────

def group_voices(prints: list) -> list[int]:
    """Each line's speaker group, 0, 1, … in order of first appearance: average-linkage grouping of the voice prints
    while two groups are at least GROUP_SIMILARITY alike. A line too short for a print joins the line nearest before
    it (else after it)."""
    have = [i for i, p in enumerate(prints) if p is not None]
    if not have:
        return [0] * len(prints)
    vectors = np.stack([np.asarray(prints[i], np.float32) for i in have])
    groups = [[k] for k in range(len(have))]
    while len(groups) > 1:
        best, pair = -1.0, None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                sim = float((vectors[groups[a]] @ vectors[groups[b]].T).mean())
                if sim > best:
                    best, pair = sim, (a, b)
        if best < GROUP_SIMILARITY:
            break
        a, b = pair
        groups[a] += groups.pop(b)
    of = {have[k]: g for g, members in enumerate(groups) for k in members}
    raw = []
    for i in range(len(prints)):
        if i not in of:
            before = [j for j in have if j < i]
            of[i] = of[before[-1]] if before else of[have[0]]
        raw.append(of[i])
    renumber: dict[int, int] = {}
    return [renumber.setdefault(g, len(renumber)) for g in raw]


# ── one episode ──────────────────────────────────────────────────

async def transcribe(vocals: str, audio: str, names: list[str], taken_labels) -> list[DiarizedSegment]:
    """The episode's lines (one per clip with words), each with a SPEAKER_XX the project hasn't used and the voice
    it sounds like. Raises when the child fails (the caller asks Gemini)."""
    spans = await asr._detect_speech_segments(vocals)
    if not spans:
        return []
    began = time.monotonic()
    result = await _run_child({"vocals": vocals, "audio": audio or vocals, "names": names,
                               "spans": [list(s) for s in spans]},
                              timeout=60.0 + 2.0 * max(e for _, e in spans))
    kept = []
    for (start, end), clip in zip(spans, result["clips"]):
        text = clean_text(clip.get("text", ""), names)
        if not text or asr._speech_chars(text) / max(end - start, 0.1) > asr.MAX_CHARS_PER_SECOND:
            continue   # nothing said, or a loop
        kept.append((start, end, text, clip.get("voice"), clip.get("print")))
    groups = group_voices([p for *_, p in kept])
    allocator = asr._LabelAllocator(taken_labels)
    labels: dict[int, str] = {}
    kinds: dict[int, Counter] = {}
    for g, (*_, voice, _p) in zip(groups, kept):
        if g not in labels:
            labels[g] = allocator.new_label()
        if voice:
            kinds.setdefault(g, Counter())[voice] += 1
    lines = [DiarizedSegment(speaker_label=labels[g], start_time=round(start, 3), end_time=round(end, 3),
                             gender=(kinds[g].most_common(1)[0][0] if g in kinds else "unknown"),
                             age_group="adult", source_text=text)
             for g, (start, end, text, _v, _p) in zip(groups, kept)]
    logger.info(f"Transcribed on this Mac in {time.monotonic() - began:.1f} s: {len(spans)} clips → {len(lines)} "
                f"lines, {len(labels)} speakers")
    return lines


async def _run_child(request: dict, timeout: float) -> dict:
    """transcribe_clips in a child process, one at a time. Raises when it fails or takes longer than `timeout`."""
    with tempfile.TemporaryDirectory(prefix="transcribing-") as tmp:
        req, res = Path(tmp) / "request.json", Path(tmp) / "result.json"
        req.write_text(json.dumps(request, ensure_ascii=False))
        if getattr(sys, "frozen", False):
            command = [sys.executable, CHILD_FLAG, str(req), str(res)]
        else:
            command = [sys.executable, "-m", __name__, str(req), str(res)]
        async with _child_lock:
            child = await asyncio.create_subprocess_exec(
                *command, cwd=str(Path(__file__).resolve().parents[2]),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                _, stderr = await asyncio.wait_for(child.communicate(), timeout)
            except BaseException:   # a timeout, or the job cancelled: the child mustn't outlive it
                child.kill()
                await child.wait()
                raise
            if child.returncode != 0:
                raise RuntimeError(f"exit {child.returncode}: {stderr.decode(errors='replace')[-400:]}")
        return json.loads(res.read_text())


def transcribe_clips(request: dict) -> dict:
    """The child: for each span, its text (Qwen3-ASR, from the original sound, given the names), the voice it sounds
    like (man/woman) and its voice print (both from the vocals)."""
    import sherpa_onnx
    from app.services.characters import models, voiceprint

    asr_dir, tagger_dir = model_dir(ASR), model_dir(TAGGER)
    recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
        conv_frontend=str(asr_dir / "conv_frontend.onnx"), encoder=str(asr_dir / "encoder.int8.onnx"),
        decoder=str(asr_dir / "decoder.int8.onnx"), tokenizer=str(asr_dir / "tokenizer"),
        num_threads=models.THREADS, hotwords=",".join(request.get("names") or []))
    tagger = sherpa_onnx.AudioTagging(sherpa_onnx.AudioTaggingConfig(
        model=sherpa_onnx.AudioTaggingModelConfig(
            zipformer=sherpa_onnx.OfflineZipformerAudioTaggingModelConfig(model=str(tagger_dir / "model.int8.onnx")),
            num_threads=models.THREADS),
        labels=str(tagger_dir / "class_labels_indices.csv"), top_k=50))
    rate = voiceprint.RATE
    vocals = voiceprint.read_audio(request["vocals"])
    audio = vocals if request["audio"] == request["vocals"] else voiceprint.read_audio(request["audio"])
    clips = []
    for start, end in request["spans"]:
        lo, hi = int(start * rate), int(end * rate)
        heard = np.asarray(audio[lo:hi], np.float32) / 32768
        voice_part = vocals[lo:hi]
        stream = recognizer.create_stream()
        stream.accept_waveform(rate, heard)
        recognizer.decode_stream(stream)
        tag = tagger.create_stream()
        tag.accept_waveform(rate, np.asarray(voice_part, np.float32) / 32768)
        events = {e.name: e.prob for e in tagger.compute(tag)}
        male, female = events.get(MALE, 0.0), events.get(FEMALE, 0.0)
        print_ = voiceprint.voice_print(voice_part)
        clips.append({"text": stream.result.text,
                      "voice": ("man" if male >= female else "woman") if male or female else None,
                      "print": print_.tolist() if print_ is not None else None})
    return {"clips": clips}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    request = json.loads(Path(argv[0]).read_text())
    Path(argv[1]).write_text(json.dumps(transcribe_clips(request), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
