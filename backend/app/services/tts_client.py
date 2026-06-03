"""
app/services/tts_client.py
TTS client — tries VoxCPM2 first, falls back to Gemini TTS, then mock silence.
"""
import logging
import wave
import httpx
import asyncio
import soundfile as sf
import numpy as np
from pathlib import Path
from app.core.config import settings

logger = logging.getLogger(__name__)

# Stable Gemini TTS model (dedicated audio output, not a chat model)
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"

# Gemini prebuilt voices available for the TTS model
_GEMINI_VOICES = {
    "male_young":   "Puck",
    "male_adult":   "Fenrir",
    "male_senior":  "Charon",
    "female_young": "Aoede",
    "female_adult": "Kore",
    "female_senior":"Laomedeia",
    "default":      "Puck",
}


def _pick_gemini_voice(voice_design: str) -> str:
    """Map a voice_design description string to a Gemini prebuilt voice name."""
    vd = (voice_design or "").lower()
    is_female = any(w in vd for w in ("female", "woman", "girl"))
    is_male   = any(w in vd for w in ("male", "man", "boy"))
    is_child  = any(w in vd for w in ("child", "young", "kid"))
    is_senior = any(w in vd for w in ("old", "senior", "elder"))

    if is_female:
        if is_child:   return _GEMINI_VOICES["female_young"]
        if is_senior:  return _GEMINI_VOICES["female_senior"]
        return _GEMINI_VOICES["female_adult"]
    if is_male:
        if is_child:   return _GEMINI_VOICES["male_young"]
        if is_senior:  return _GEMINI_VOICES["male_senior"]
        return _GEMINI_VOICES["male_adult"]
    if is_child:
        return _GEMINI_VOICES["female_young"]
    return _GEMINI_VOICES["default"]


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int, output_path: str) -> None:
    """Wrap raw 16-bit mono PCM bytes in a proper WAV container."""
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)   # mono
        wf.setsampwidth(2)   # 16-bit = 2 bytes/sample
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


def _get_wav_duration(wav_path: str) -> float:
    """Return duration of a WAV file in seconds."""
    try:
        info = sf.info(wav_path)
        return info.duration
    except Exception:
        return 0.0


class VoxCPM2Client:
    """
    TTS client with fallback chain:
      VoxCPM2 GPU server → Gemini TTS → mock silence
    """

    def __init__(self):
        self.base_url = settings.VOXCPM2_API_URL.rstrip("/")
        self.api_key  = settings.VOXCPM2_API_KEY
        # Generous timeout: serverless GPU hosts (Modal etc.) cold-start the
        # container + load the model on the first call after idle, which can
        # take 2–3 minutes before any audio is returned.
        self.timeout  = 300.0

        # Backend protocol: "rest" (our Modal server) or "gradio" (Colab Gradio
        # app). Auto-detect from the URL when not explicitly configured.
        configured = (settings.VOXCPM2_BACKEND or "").lower().strip()
        if configured in ("gradio", "rest"):
            self.backend = configured
        else:
            self.backend = "gradio" if "gradio" in self.base_url else "rest"

        # Cached gradio_client.Client (recreated if the URL changes)
        self._gradio_client = None
        self._gradio_url = None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def health_check(self) -> bool:
        if not self.base_url:
            return False
        # Gradio apps have no /health route — treat a configured URL as available
        # (the real connection is validated on the first synthesis call).
        if self.backend == "gradio":
            return True
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
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
        reference_audio_path: str = "",
        reference_transcript: str = "",
        seed: int = -1,
        max_retries: int = 3,
    ) -> dict:
        """
        Synthesize speech.  Fallback chain:
          1. VoxCPM2 (if VOXCPM2_API_URL is set)
          2. Gemini TTS (if GEMINI_API_KEY is set)
          3. Mock silence

        Voice modes (decided by what's passed):
          - voice_design only                       -> Voice Design
          - reference_audio_path                    -> Controllable Cloning
          - reference_audio_path + transcript       -> Ultimate Cloning
        """
        if self.base_url:
            if self.backend == "gradio":
                result = await self._gradio_synthesize(
                    text, voice_design, output_path, cfg_value, inference_timesteps,
                    reference_audio_path, reference_transcript, seed,
                )
            else:
                result = await self._voxcpm2_synthesize(
                    text, voice_design, output_path, cfg_value, inference_timesteps,
                    reference_audio_path, reference_transcript, max_retries,
                )
            if result["success"]:
                return result
            logger.warning(f"VoxCPM2 failed ({result['error']}) — falling back to Gemini TTS")

        return await self._gemini_synthesize(text, output_path, voice_design=voice_design)

    # ── Gradio (Colab gradio.live app) ───────────────────────────────────

    def _get_gradio_client(self):
        from gradio_client import Client
        if self._gradio_client is None or self._gradio_url != self.base_url:
            logger.info(f"Connecting gradio_client to {self.base_url}")
            self._gradio_client = Client(self.base_url)
            self._gradio_url = self.base_url
        return self._gradio_client

    def _gradio_call(
        self, text, voice_design, output_path, cfg_value, inference_timesteps,
        reference_audio_path, reference_transcript, seed,
    ) -> dict:
        """Blocking gradio_client call — run inside a thread."""
        import shutil
        from gradio_client import handle_file

        client = self._get_gradio_client()
        steps = int(inference_timesteps)
        has_ref = bool(reference_audio_path) and Path(reference_audio_path).exists()

        # A fixed seed (>= 0) locks the voice identity for consistency across
        # every line of a speaker. -1 leaves it random.
        seed = int(seed) if seed is not None else -1
        locked = seed >= 0

        if has_ref and reference_transcript:
            out = client.predict(
                text=text, ref_audio=handle_file(reference_audio_path),
                transcript=reference_transcript, cfg=cfg_value, steps=steps,
                seed=seed, locked=locked, api_name="/ultimate_clone",
            )
        elif has_ref:
            out = client.predict(
                text=text, ref_audio=handle_file(reference_audio_path),
                style=voice_design or "", cfg=cfg_value, steps=steps,
                seed=seed, locked=locked, api_name="/voice_clone",
            )
        elif voice_design:
            out = client.predict(
                description=voice_design, text=text, cfg=cfg_value, steps=steps,
                seed=seed, locked=locked, api_name="/voice_design",
            )
        else:
            out = client.predict(
                text=text, cfg=cfg_value, steps=steps,
                seed=seed, locked=locked, api_name="/tts_generate",
            )

        # Each endpoint returns (filepath, seed)
        src = out[0] if isinstance(out, (list, tuple)) else out
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out_p)
        duration = _get_wav_duration(str(out_p))
        logger.info(f"Gradio VoxCPM2 saved: {out_p.name} ({duration:.1f}s)")
        return {"success": True, "audio_path": str(out_p), "duration_secs": duration, "error": ""}

    async def _gradio_synthesize(
        self, text, voice_design, output_path, cfg_value, inference_timesteps,
        reference_audio_path, reference_transcript, seed,
    ) -> dict:
        try:
            logger.info(f"Gradio VoxCPM2 TTS (seed={seed}): {text[:50]}...")
            return await asyncio.to_thread(
                self._gradio_call, text, voice_design, output_path, cfg_value,
                inference_timesteps, reference_audio_path, reference_transcript, seed,
            )
        except Exception as e:
            logger.warning(f"Gradio VoxCPM2 failed: {e}")
            return {"success": False, "audio_path": "", "duration_secs": 0, "error": str(e)}

    # ── VoxCPM2 ──────────────────────────────────────────────────────────

    async def _voxcpm2_synthesize(
        self,
        text: str,
        voice_design: str,
        output_path: str,
        cfg_value: float,
        inference_timesteps: int,
        reference_audio_path: str,
        reference_transcript: str,
        max_retries: int,
    ) -> dict:
        # voice_design becomes the "(...)" prefix (works for both Voice Design
        # and as style guidance in Controllable Cloning).
        full_text = f"({voice_design}){text}" if voice_design else text
        payload = {
            "text": full_text,
            "cfg_value": cfg_value,
            "inference_timesteps": inference_timesteps,
        }

        # Attach reference audio (base64) for cloning modes.
        if reference_audio_path and Path(reference_audio_path).exists():
            import base64
            payload["reference_audio_b64"] = base64.b64encode(
                Path(reference_audio_path).read_bytes()
            ).decode("ascii")
            if reference_transcript:
                payload["reference_transcript"] = reference_transcript

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"VoxCPM2 TTS (attempt {attempt}): {text[:50]}...")
                # follow_redirects: serverless hosts (Modal) return a 303 to
                # reconnect after a long cold start — httpx must follow it,
                # otherwise we'd treat the redirect as success and save 0 bytes.
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                    resp = await client.post(
                        f"{self.base_url}/tts",
                        json=payload,
                        headers=self._headers(),
                    )
                    resp.raise_for_status()

                out_path = Path(output_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(resp.content)

                duration = _get_wav_duration(str(out_path))
                logger.info(f"VoxCPM2 saved: {out_path.name} ({duration:.1f}s)")
                return {"success": True, "audio_path": str(out_path), "duration_secs": duration, "error": ""}

            except Exception as e:
                logger.warning(f"VoxCPM2 attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)

        return {"success": False, "audio_path": "", "duration_secs": 0, "error": "VoxCPM2 unreachable"}

    # ── Gemini TTS ────────────────────────────────────────────────────────

    async def _gemini_synthesize(self, text: str, output_path: str, voice_design: str = "") -> dict:
        """
        Generate WAV using Gemini TTS API (gemini-2.5-flash-preview-tts).
        Gemini returns raw 16-bit PCM; we convert to a proper WAV file.
        """
        if not settings.GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not set — using mock TTS.")
            return await self._mock_synthesize(text, output_path)

        voice_name = _pick_gemini_voice(voice_design)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_TTS_MODEL}:generateContent"
        )
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.GEMINI_API_KEY,
        }
        # gemini-2.5-flash-preview-tts does not accept speakingRate in speechConfig.
        # Control pace via the instruction prompt instead.
        speed = settings.GEMINI_TTS_SPEED
        if speed >= 1.4:
            pace_instruction = "Speak quickly and energetically, at a fast but clear pace."
        elif speed >= 1.15:
            pace_instruction = "Speak at a brisk, natural pace — slightly faster than normal."
        elif speed <= 0.85:
            pace_instruction = "Speak slowly and clearly."
        else:
            pace_instruction = "Speak at a natural, conversational pace."

        prompt = (
            f"{pace_instruction} "
            "Read the following text out loud directly — no introductions, "
            "no translations, just read it:\n\n"
            f"{text}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": voice_name}
                    }
                },
            },
        }

        try:
            logger.info(f"Gemini TTS ({GEMINI_TTS_MODEL}, voice={voice_name}): {text[:60]}...")
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 429:
                logger.warning("Gemini TTS rate-limited — falling back to mock")
                return await self._mock_synthesize(text, output_path)

            resp.raise_for_status()
            data = resp.json()

            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    if "inlineData" not in part:
                        continue
                    inline = part["inlineData"]
                    mime   = inline.get("mimeType", "")
                    if not mime.startswith("audio/"):
                        continue

                    import base64
                    pcm_bytes = base64.b64decode(inline["data"])

                    # Parse sample rate from mimeType e.g. "audio/L16;codec=pcm;rate=24000"
                    sample_rate = 24000
                    for chunk in mime.split(";"):
                        chunk = chunk.strip()
                        if chunk.startswith("rate="):
                            try:
                                sample_rate = int(chunk.split("=", 1)[1])
                            except ValueError:
                                pass

                    out_path = Path(output_path)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    _pcm_to_wav(pcm_bytes, sample_rate, str(out_path))

                    duration = _get_wav_duration(str(out_path))
                    logger.info(f"Gemini TTS saved: {out_path.name} ({duration:.1f}s, {sample_rate}Hz)")
                    return {
                        "success": True,
                        "audio_path": str(out_path),
                        "duration_secs": duration,
                        "error": "",
                    }

            logger.warning(f"Gemini TTS returned no audio. Response: {data}")
            return await self._mock_synthesize(text, output_path)

        except Exception as e:
            logger.error(f"Gemini TTS failed: {e}")
            return {"success": False, "audio_path": "", "duration_secs": 0, "error": str(e)}

    # ── Mock (silent WAV) ─────────────────────────────────────────────────

    async def _mock_synthesize(self, text: str, output_path: str) -> dict:
        logger.warning("Using MOCK TTS — no real audio will be produced.")
        duration = max(1.0, len(text) / 10)
        sample_rate = 22050
        silence = np.zeros(int(duration * sample_rate), dtype=np.float32)

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), silence, sample_rate)

        return {"success": True, "audio_path": str(out_path), "duration_secs": duration, "error": ""}

    async def synthesize_batch(self, segments: list, output_dir: str, max_concurrent: int = 3) -> list:
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

        return await asyncio.gather(*[synth_one(s) for s in segments])


# Singleton
tts_client = VoxCPM2Client()
