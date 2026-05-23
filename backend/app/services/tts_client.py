"""
app/services/tts_client.py
Client for the VoxCPM2 TTS server (running on Lightning AI / RunPod GPU).
Sends Khmer text + voice design prompt → receives .wav audio bytes.
"""
import logging
import httpx
import asyncio
import soundfile as sf
import numpy as np
from pathlib import Path
from app.core.config import settings

logger = logging.getLogger(__name__)


class VoxCPM2Client:
    """
    Async HTTP client for the VoxCPM2 FastAPI server.
    Handles retries, timeouts, and saving audio to disk.
    """

    def __init__(self):
        self.base_url = settings.VOXCPM2_API_URL.rstrip("/")
        self.api_key  = settings.VOXCPM2_API_KEY
        self.timeout  = 120.0   # TTS generation can take time

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def health_check(self) -> bool:
        """Check if the VoxCPM2 server is reachable."""
        if not self.base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False

    async def synthesize(
        self,
        text: str,
        voice_design: str = "",
        output_path: str = "",
        cfg_value: float = 2.0,
        inference_timesteps: int = 10,
        max_retries: int = 3,
    ) -> dict:
        """
        Send text to VoxCPM2 TTS server and save the returned audio.

        Args:
            text:                Khmer text to synthesize
            voice_design:        e.g. "A young male, confident voice"
            output_path:         Where to save the .wav file
            cfg_value:           Classifier-free guidance strength
            inference_timesteps: Diffusion steps (more = slower but better)
            max_retries:         Number of retry attempts on failure

        Returns:
            dict with keys: success, audio_path, duration_secs, error
        """
        if not self.base_url:
            logger.warning("VOXCPM2_API_URL not set — using mock TTS.")
            return await self._mock_synthesize(text, output_path)

        # VoxCPM2 voice design: prepend in parentheses if provided
        full_text = f"({voice_design}){text}" if voice_design else text

        payload = {
            "text": full_text,
            "cfg_value": cfg_value,
            "inference_timesteps": inference_timesteps,
        }

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"TTS request (attempt {attempt}): {text[:50]}...")
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/tts",
                        json=payload,
                        headers=self._headers(),
                    )
                    resp.raise_for_status()

                    # Save the WAV audio bytes to disk
                    audio_bytes = resp.content
                    out_path = Path(output_path)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(audio_bytes)

                    # Get duration
                    duration = _get_wav_duration(str(out_path))
                    logger.info(f"TTS saved: {out_path.name} ({duration:.1f}s)")

                    return {
                        "success": True,
                        "audio_path": str(out_path),
                        "duration_secs": duration,
                        "error": "",
                    }

            except httpx.HTTPStatusError as e:
                logger.error(f"TTS server HTTP error: {e.response.status_code}")
                if attempt == max_retries:
                    return {"success": False, "audio_path": "", "duration_secs": 0,
                            "error": f"HTTP {e.response.status_code}"}
                await asyncio.sleep(2 ** attempt)   # exponential backoff

            except Exception as e:
                logger.error(f"TTS request failed: {e}")
                if attempt == max_retries:
                    return {"success": False, "audio_path": "", "duration_secs": 0,
                            "error": str(e)}
                await asyncio.sleep(2 ** attempt)

    async def synthesize_batch(
        self,
        segments: list,
        output_dir: str,
        max_concurrent: int = 3,
    ) -> list:
        """
        Synthesize multiple segments concurrently.

        Args:
            segments:       List of dicts: {id, text, voice_design}
            output_dir:     Directory to save .wav files
            max_concurrent: Max parallel TTS requests

        Returns:
            List of result dicts
        """
        sem = asyncio.Semaphore(max_concurrent)
        output_dir = Path(output_dir)

        async def synth_one(seg: dict) -> dict:
            async with sem:
                out_path = output_dir / f"tts_{seg['id']}.wav"
                result = await self.synthesize(
                    text=seg["text"],
                    voice_design=seg.get("voice_design", ""),
                    output_path=str(out_path),
                )
                result["segment_id"] = seg["id"]
                return result

        tasks = [synth_one(seg) for seg in segments]
        return await asyncio.gather(*tasks)

    async def _mock_synthesize(self, text: str, output_path: str) -> dict:
        """
        Generate a silent mock WAV for testing without GPU server.
        Produces 1 second of silence per 10 characters of text.
        """
        logger.warning("Using MOCK TTS — set VOXCPM2_API_URL to use real synthesis.")
        duration = max(1.0, len(text) / 10)
        sample_rate = 22050
        silence = np.zeros(int(duration * sample_rate), dtype=np.float32)

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), silence, sample_rate)

        return {
            "success": True,
            "audio_path": str(out_path),
            "duration_secs": duration,
            "error": "",
        }


def _get_wav_duration(wav_path: str) -> float:
    """Get duration of a WAV file in seconds."""
    try:
        info = sf.info(wav_path)
        return info.duration
    except Exception:
        return 0.0


# Singleton client instance
tts_client = VoxCPM2Client()
