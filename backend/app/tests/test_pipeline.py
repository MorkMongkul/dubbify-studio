"""
test_pipeline.py
────────────────
Standalone script to test the full ASR + Translation pipeline.
No VoxCPM2, no database, no FastAPI server needed.
No model downloads — everything runs via cloud APIs.

Services used:
  - pyannoteAI cloud API  → speaker diarization  (PYANNOTEAI_TOKEN)
  - HuggingFace API       → Whisper ASR           (HF_TOKEN)
  - deep-translator       → ZH→EN + ZH→KM         (no token needed)

Run with a real Chinese movie clip:
    python test_pipeline.py --input clip.mp4
    python test_pipeline.py --input clip.wav --lang zh
    python test_pipeline.py --demo              # silent tone, tests flow only

Output saved to: pipeline_test_output.json
"""
import asyncio
import argparse
import json
import os
import sys
import time
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Colour helpers for terminal output ────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ok(msg):   logger.info(f"{GREEN}✓ {msg}{RESET}")
def warn(msg): logger.warning(f"{YELLOW}⚠ {msg}{RESET}")
def err(msg):  logger.error(f"{RED}✗ {msg}{RESET}")
def info(msg): logger.info(f"{CYAN}→ {msg}{RESET}")
def sep():     print(f"\n{BOLD}{'─' * 60}{RESET}\n")


# ── Stage 0: Generate demo audio (no real video needed) ───────

def generate_demo_audio(output_path: str) -> str:
    """
    Generate a short test audio file with a sine wave tone.
    Used when no real video/audio is provided.
    Whisper will transcribe it as silence/noise — that's expected.
    """
    try:
        import numpy as np
        import soundfile as sf

        logger.info("Generating demo audio tone (5 seconds)...")
        sample_rate = 16000
        duration    = 5.0
        frequency   = 440.0   # A4 note

        t   = np.linspace(0, duration, int(sample_rate * duration))
        wav = (np.sin(2 * np.pi * frequency * t) * 0.3).astype(np.float32)

        sf.write(output_path, wav, sample_rate)
        ok(f"Demo audio created: {output_path}")
        return output_path

    except ImportError:
        err("numpy/soundfile not installed. Run: pip install numpy soundfile")
        sys.exit(1)


# ── Stage 1: Audio extraction ──────────────────────────────────

async def stage_extract_audio(input_path: str, work_dir: str) -> str:
    """Extract audio from video using ffmpeg. Skip if input is already a WAV."""
    sep()
    info("STAGE 1 — Audio Extraction")

    suffix = Path(input_path).suffix.lower()

    if suffix in (".wav", ".mp3", ".flac", ".m4a"):
        ok(f"Input is already audio — skipping extraction: {input_path}")
        return input_path

    audio_path = str(Path(work_dir) / "extracted_audio.wav")

    try:
        from app.services.audio_extractor import extract_audio, get_video_duration

        duration = await get_video_duration(input_path)
        info(f"Video duration: {duration:.1f}s")

        t0 = time.time()
        result = await extract_audio(input_path, work_dir)
        ok(f"Audio extracted in {time.time()-t0:.1f}s → {result}")
        return result

    except Exception as e:
        err(f"Audio extraction failed: {e}")
        err("Make sure ffmpeg is installed: brew install ffmpeg")
        sys.exit(1)


# ── Stage 2: Speaker Diarization ──────────────────────────────

async def stage_diarize(audio_path: str) -> list:
    """
    Run pyannote speaker diarization.
    Falls back to mock (2 speakers) if pyannote is not available or
    HF_TOKEN is missing / license not accepted.
    """
    sep()
    info("STAGE 2 — Speaker Diarization")

    hf_token = os.getenv("HF_TOKEN", "")
    if not hf_token:
        warn("HF_TOKEN not set in .env — using mock diarizer (2 fake speakers)")
        warn("Set HF_TOKEN and accept pyannote license on HuggingFace to use real diarization")

    try:
        from app.services.diarizer import diarize_audio

        t0 = time.time()
        segments = await diarize_audio(audio_path)
        elapsed  = time.time() - t0

        speakers = set(s.speaker_label for s in segments)
        ok(f"Diarization complete in {elapsed:.1f}s")
        ok(f"Found {len(speakers)} speaker(s): {', '.join(sorted(speakers))}")
        ok(f"Total segments: {len(segments)}")

        for seg in segments[:5]:   # show first 5
            print(f"    [{seg.start_time:.1f}s → {seg.end_time:.1f}s] "
                  f"{seg.speaker_label} | {seg.gender} | {seg.age_group}")
        if len(segments) > 5:
            print(f"    ... and {len(segments)-5} more segments")

        return segments

    except Exception as e:
        err(f"Diarization failed: {e}")
        warn("Falling back to single-speaker mock")
        # Return a single segment covering the whole file
        from app.services.diarizer import DiarizedSegment
        import soundfile as sf
        info_obj = sf.info(audio_path)
        return [DiarizedSegment(
            speaker_label="SPEAKER_00",
            start_time=0.0,
            end_time=info_obj.duration,
            gender="unknown",
            age_group="adult",
        )]


# ── Stage 3: ASR Transcription ────────────────────────────────

async def stage_transcribe(audio_path: str, diarized: list, source_lang: str) -> list:
    """
    Run Whisper large-v3 via HuggingFace transformers pipeline.
    First run downloads ~3GB from HuggingFace Hub.
    """
    sep()
    info("STAGE 3 — ASR Transcription (Whisper large-v3 via HuggingFace)")
    info(f"Source language: {source_lang}")
    info("First run will download ~3GB — subsequent runs use cache")

    try:
        from app.services.transcriber import transcribe_segments

        t0 = time.time()
        transcribed = await transcribe_segments(
            audio_path=audio_path,
            diarized_segments=diarized,
            source_lang=source_lang,
        )
        elapsed = time.time() - t0

        ok(f"Transcription complete in {elapsed:.1f}s")
        ok(f"Transcribed {len(transcribed)} segments")

        for seg in transcribed[:5]:
            print(f"    [{seg.start_time:.1f}s] {seg.speaker_label}: "
                  f"{seg.source_text[:80] or '(empty)'}")
        if len(transcribed) > 5:
            print(f"    ... and {len(transcribed)-5} more")

        return transcribed

    except Exception as e:
        err(f"Transcription failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


# ── Stage 4: Translation ──────────────────────────────────────

async def stage_translate(transcribed: list, source_lang: str) -> list:
    """
    Translate all segments to English and Khmer in parallel.
    Uses TRANSLATION_BACKEND from .env (default: deep = Google Translate).
    """
    sep()
    backend = os.getenv("TRANSLATION_BACKEND", "deep")
    info(f"STAGE 4 — Translation (backend: {backend})")
    info("Translating to English and Khmer in parallel...")

    try:
        from app.services.translator import translate_batch

        source_texts = [seg.source_text for seg in transcribed]

        # Skip empty segments
        non_empty = [t for t in source_texts if t.strip()]
        if not non_empty:
            warn("All segments are empty — nothing to translate")
            warn("This is expected if you used --demo (tone audio has no speech)")
            return []

        t0 = time.time()

        # Run both translations concurrently
        en_texts, km_texts = await asyncio.gather(
            translate_batch(source_texts, source_lang, "en"),
            translate_batch(source_texts, source_lang, "km"),
        )

        elapsed = time.time() - t0
        ok(f"Translation complete in {elapsed:.1f}s")

        # Combine everything
        results = []
        for i, seg in enumerate(transcribed):
            results.append({
                "speaker":      seg.speaker_label,
                "gender":       seg.gender,
                "age_group":    seg.age_group,
                "start_time":   seg.start_time,
                "end_time":     seg.end_time,
                "source_text":  seg.source_text,
                "english_text": en_texts[i] if i < len(en_texts) else "",
                "khmer_text":   km_texts[i] if i < len(km_texts) else "",
            })

        # Print sample
        for r in results[:3]:
            print(f"\n    [{r['start_time']:.1f}s] {r['speaker']} ({r['gender']}, {r['age_group']})")
            print(f"    ZH: {r['source_text'][:80] or '(empty)'}")
            print(f"    EN: {r['english_text'][:80] or '(empty)'}")
            print(f"    KM: {r['khmer_text'][:80] or '(empty)'}")

        return results

    except Exception as e:
        err(f"Translation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


# ── Save output ───────────────────────────────────────────────

def save_output(results: list, output_file: str):
    sep()
    info("Saving results...")

    output = {
        "pipeline": "KhmerDubber ASR + Translation",
        "total_segments": len(results),
        "segments": results,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    ok(f"Results saved → {output_file}")
    info(f"Open the file to review all {len(results)} segments")


# ── Main ──────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="Test the KhmerDubber ASR + Translation pipeline"
    )
    parser.add_argument(
        "--input", "-i",
        help="Path to video (.mp4, .mkv) or audio (.wav, .mp3) file",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Generate a demo audio tone instead of using a real file",
    )
    parser.add_argument(
        "--lang", "-l",
        default="zh",
        help="Source language ISO code (default: zh for Chinese)",
    )
    parser.add_argument(
        "--output", "-o",
        default="pipeline_test_output.json",
        help="Output JSON file path (default: pipeline_test_output.json)",
    )
    args = parser.parse_args()

    if not args.input and not args.demo:
        parser.print_help()
        print(f"\n{YELLOW}Examples:{RESET}")
        print("  python test_pipeline.py --demo")
        print("  python test_pipeline.py --input movie.mp4")
        print("  python test_pipeline.py --input audio.wav --lang zh")
        sys.exit(0)

    # ── Print header ──────────────────────────────────────────
    print(f"\n{BOLD}{'═' * 60}{RESET}")
    print(f"{BOLD}  KhmerDubber — Pipeline Test{RESET}")
    print(f"{BOLD}{'═' * 60}{RESET}")
    print(f"  Diarization         : {'pyannoteAI cloud ✓' if os.getenv('PYANNOTEAI_TOKEN') else 'mock (no PYANNOTEAI_TOKEN)'}")
    print(f"  ASR (Whisper)       : {'HuggingFace API ✓' if os.getenv('HF_TOKEN') else 'mock (no HF_TOKEN)'}")
    print(f"  Translation backend : {os.getenv('TRANSLATION_BACKEND', 'deep')}")
    print(f"  Source language     : {args.lang}")
    print(f"  Output file         : {args.output}")
    print(f"{BOLD}{'═' * 60}{RESET}\n")

    # Work directory for intermediate files
    work_dir = "./pipeline_test_workdir"
    os.makedirs(work_dir, exist_ok=True)

    # ── Get input file ────────────────────────────────────────
    if args.demo:
        input_path = os.path.join(work_dir, "demo_audio.wav")
        input_path = generate_demo_audio(input_path)
    else:
        input_path = args.input
        if not os.path.exists(input_path):
            err(f"File not found: {input_path}")
            sys.exit(1)
        ok(f"Input file: {input_path} ({Path(input_path).stat().st_size / 1e6:.1f} MB)")

    # ── Run pipeline stages ───────────────────────────────────
    total_start = time.time()

    audio_path  = await stage_extract_audio(input_path, work_dir)
    diarized    = await stage_diarize(audio_path)
    transcribed = await stage_transcribe(audio_path, diarized, args.lang)
    results     = await stage_translate(transcribed, args.lang)

    # ── Done ──────────────────────────────────────────────────
    sep()
    total_elapsed = time.time() - total_start
    save_output(results, args.output)

    print(f"\n{BOLD}{GREEN}{'═' * 60}{RESET}")
    print(f"{BOLD}{GREEN}  Pipeline complete in {total_elapsed:.1f}s{RESET}")
    print(f"{BOLD}{GREEN}  {len(results)} segments ready for VoxCPM2 TTS{RESET}")
    print(f"{BOLD}{GREEN}{'═' * 60}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())