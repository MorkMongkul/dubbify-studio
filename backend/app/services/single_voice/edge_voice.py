"""One line's voice from Microsoft Edge's read-aloud service (the edge-tts package, LGPL-3.0; no key). It is
unofficial: Microsoft can change or block it, so a failure says so plainly and is never replaced by silence or another
engine's voice. Only the Khmer text is sent. Measured 2026-11-05: 1.4–1.9 s a line for both Khmer voices."""
import asyncio
import logging
import subprocess
import tempfile
import time
from pathlib import Path

from app.core.binaries import ffmpeg

logger = logging.getLogger(__name__)

ATTEMPTS = 4
BACKOFF_SECS = 2.0   # doubled after each failed attempt
_sleep = asyncio.sleep


def _communicate(text: str, voice: str, rate: str):
    import edge_tts
    return edge_tts.Communicate(text, voice, rate=rate)


def _rate(rate: int) -> str:
    return f"{'+' if rate >= 0 else '-'}{abs(int(rate))}%"


def _failed(error: str) -> dict:
    return {"success": False, "audio_path": "", "duration_secs": 0.0, "error": error, "backend": "edge"}


async def synthesize(text: str, output_path: str, voice: str, rate: int) -> dict:
    """`text` spoken by `voice` at `rate` percent, saved as a WAV at `output_path`. The same answer as
    tts_client.synthesize; never raises."""
    if not (text or "").strip():
        return _failed("There's no Khmer text to say")
    error = ""
    delay = BACKOFF_SECS
    for attempt in range(1, ATTEMPTS + 1):
        with tempfile.TemporaryDirectory(prefix="edge-") as tmp:
            mp3 = Path(tmp) / "voice.mp3"
            try:
                began = time.monotonic()
                await _communicate(text, voice, _rate(rate)).save(str(mp3))
                if not mp3.is_file() or mp3.stat().st_size == 0:
                    raise RuntimeError("an empty answer")
                done = await asyncio.to_thread(
                    subprocess.run, [ffmpeg(), "-v", "error", "-y", "-i", str(mp3), "-ac", "1", "-ar", "24000",
                                     output_path], capture_output=True)
                if done.returncode != 0:
                    raise RuntimeError(done.stderr.decode(errors="replace")[-200:])
                from app.services.audio_extractor import get_video_duration
                seconds = await get_video_duration(output_path)
                logger.debug(f"Edge voice in {time.monotonic() - began:.1f} s")
                return {"success": True, "audio_path": output_path, "duration_secs": seconds, "error": "",
                        "backend": "edge"}
            except Exception as e:   # noqa: BLE001 — busy, offline, blocked: tried again, then a clear failure
                error = str(e) or type(e).__name__
                Path(output_path).unlink(missing_ok=True)
                if attempt < ATTEMPTS:
                    await _sleep(delay)
                    delay *= 2
    logger.warning(f"Edge TTS failed after {ATTEMPTS} tries: {error}")
    return _failed(f"Edge's voice service didn't answer ({error}) — try again later")
