"""
app/services/tts_client.py
TTS client — tries VoxCPM2 first, falls back to Gemini TTS, then mock silence.

VoxCPM2 has 2 possible backends, picked in VoxCPM2Client.__init__:
  - VOXCPM2_API_URL set   -> a Gradio app (e.g. a Colab notebook's gradio.live
                             URL running the VoxCPM HF model)
  - VOXCPM2_API_URL blank -> free public HF Space (openbmb/VoxCPM-Demo),
                             automatic, no setup — used whenever the Gradio
                             app isn't running that session
"""
import logging
import time
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
        configured_url = settings.VOXCPM2_API_URL.rstrip("/")
        # Generous timeout: hosted GPU backends cold-start / queue on the
        # first call after idle, which can take minutes before audio returns.
        self.timeout  = 300.0

        if configured_url:
            # A Gradio app URL (e.g. Colab gradio.live running VoxCPM)
            self.base_url = configured_url
            self.backend = "gradio"
        else:
            # No VOXCPM2_API_URL configured (e.g. the Colab notebook isn't
            # running this session) — use the free public HF Space instead of
            # skipping VoxCPM2 entirely. Real VoxCPM2 quality with zero setup;
            # falls through to Gemini TTS below if the Space is busy/down.
            self.base_url = settings.VOXCPM2_HF_SPACE_FALLBACK
            self.backend = "hf_space"

        # Cached gradio_client.Client for the primary/configured backend
        # (recreated if the URL changes).
        self._gradio_client = None
        self._gradio_url = None
        # Shared HTTP client (Gemini TTS) — reuses connections across calls
        # instead of a TCP+TLS handshake per segment.
        self._http: httpx.AsyncClient | None = None
        # Separate cached client specifically for the HF Space fallback, so
        # that falling back to it while the primary backend is a Colab Gradio
        # app doesn't thrash the primary cache slot on every call.
        self._hf_fallback_client = None
        self._hf_fallback_url = None
        # When the configured Gradio URL is unreachable (Colab gradio.live
        # links expire after ~72h), skip it for a while instead of paying the
        # connect-and-fail cost on EVERY segment of a batch.
        self._primary_down_until = 0.0

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(follow_redirects=True)
        return self._http

    async def health_check(self) -> bool:
        # Gradio apps have no /health route — treat a configured URL as available
        # (the real connection is validated on the first synthesis call).
        return bool(self.base_url)

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
    ) -> dict:
        """
        Synthesize speech.  Fallback chain:
          1. VoxCPM2 — the configured Gradio app if VOXCPM2_API_URL is set,
             else the free HF Space directly
          2. Free HF Space (VOXCPM2_HF_SPACE_FALLBACK) — only tried here if step 1
             was a *configured* Gradio app that failed (e.g. Colab URL is stale/
             dead); skipped if step 1 already *was* the HF Space
          3. Gemini TTS (if GEMINI_API_KEY is set)
          4. Mock silence

        Voice modes (decided by what's passed):
          - voice_design only                       -> Voice Design
          - reference_audio_path                    -> Controllable Cloning
          - reference_audio_path + transcript       -> Ultimate Cloning
        """
        if self.backend == "gradio":
            if time.monotonic() < self._primary_down_until:
                # Known-dead Colab URL — don't burn ~5s failing on it again.
                result = {"success": False, "audio_path": "", "duration_secs": 0,
                          "error": "primary Gradio URL unreachable (cooling down)"}
            else:
                result = await self._gradio_synthesize(
                    text, voice_design, output_path, cfg_value, inference_timesteps,
                    reference_audio_path, reference_transcript, seed,
                )
                if not result["success"] and self._is_connect_error(result["error"]):
                    self._primary_down_until = time.monotonic() + 300
                    self._gradio_client = None  # force a fresh connect after cooldown
                    logger.warning(
                        f"Primary Gradio URL {self.base_url} unreachable — the "
                        "gradio.live link has likely expired. Skipping it for 5 "
                        "minutes; update VOXCPM2_API_URL with a fresh URL."
                    )
        else:  # hf_space
            result = await self._hf_space_synthesize(
                text, voice_design, output_path, cfg_value,
                reference_audio_path, reference_transcript,
            )

        if result["success"]:
            return result
        logger.warning(f"VoxCPM2 ({self.backend}) failed: {result['error']}")

        # The configured Gradio app didn't work — before giving up on VoxCPM2
        # quality entirely, try the free HF Space. Skip this if that backend
        # WAS already the HF Space (no point retrying the same thing).
        if self.backend != "hf_space":
            logger.warning("Trying free HF Space fallback before Gemini TTS...")
            fallback = await self._hf_space_synthesize(
                text, voice_design, output_path, cfg_value,
                reference_audio_path, reference_transcript,
            )
            if fallback["success"]:
                return fallback
            logger.warning(f"HF Space fallback also failed: {fallback['error']}")

        logger.warning("Falling back to Gemini TTS")
        return await self._gemini_synthesize(text, output_path, voice_design=voice_design)

    @staticmethod
    def _is_connect_error(error: str) -> bool:
        """Failure to REACH the app (vs a generation error on a live app)."""
        e = (error or "").lower()
        return any(s in e for s in (
            "could not fetch config", "connect", "connection",
            "name or service not known", "timed out", "404",
        ))

    # ── Gradio (Colab gradio.live app) ───────────────────────────────────

    def _get_gradio_client(self, url: str = None):
        """Cached gradio_client.Client for `url` (defaults to self.base_url —
        the primary/configured backend). Pass an explicit `url` to connect
        somewhere else (e.g. the HF Space fallback) without disturbing the
        primary connection's cache."""
        from gradio_client import Client
        target = url or self.base_url
        if target == self.base_url:
            if self._gradio_client is None or self._gradio_url != target:
                logger.info(f"Connecting gradio_client to {target}")
                self._gradio_client = Client(target)
                self._gradio_url = target
            return self._gradio_client

        if self._hf_fallback_client is None or self._hf_fallback_url != target:
            logger.info(f"Connecting gradio_client to {target}")
            self._hf_fallback_client = Client(target)
            self._hf_fallback_url = target
        return self._hf_fallback_client

    def _gradio_call(
        self, text, voice_design, output_path, cfg_value, inference_timesteps,
        reference_audio_path, reference_transcript, seed,
    ) -> dict:
        """Blocking gradio_client call — run inside a thread."""
        import shutil
        from gradio_client import handle_file

        from app.core.paths import resolve_media_path
        client = self._get_gradio_client()
        steps = int(inference_timesteps)
        reference_audio_path = resolve_media_path(reference_audio_path)
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

    # ── HF Space fallback (openbmb/VoxCPM-Demo, no setup required) ────────
    # Different Gradio app from the Colab one above — a single unified
    # /generate endpoint instead of 4 mode-specific ones, and no seed/
    # inference_timesteps controls (the Space doesn't expose them, so a
    # design-mode voice's identity can't be locked across calls the way the
    # Colab backend's `locked` param does — acceptable since baked design
    # voices reuse a saved reference clip via cloning after the first call
    # anyway, per voices.py's _bake_design_voice).

    def _hf_space_call(
        self, text, voice_design, output_path, cfg_value,
        reference_audio_path, reference_transcript,
    ) -> dict:
        """Blocking gradio_client call — run inside a thread."""
        import shutil
        from gradio_client import handle_file

        # Always connect to the fallback Space explicitly — self.base_url may
        # currently be a different (failed) primary backend's URL if this is
        # being called as a secondary fallback, not the configured backend.
        from app.core.paths import resolve_media_path
        client = self._get_gradio_client(settings.VOXCPM2_HF_SPACE_FALLBACK)
        reference_audio_path = resolve_media_path(reference_audio_path)
        has_ref = bool(reference_audio_path) and Path(reference_audio_path).exists()
        use_prompt_text = has_ref and bool(reference_transcript)

        out = client.predict(
            text_input=text,
            control_instruction=voice_design or "",
            reference_wav_path_input=handle_file(reference_audio_path) if has_ref else None,
            use_prompt_text=use_prompt_text,
            prompt_text_input=reference_transcript if use_prompt_text else "",
            cfg_value_input=cfg_value,
            do_normalize=False,
            denoise=False,
            api_name="/generate",
        )

        # /generate returns a single filepath (unlike the Colab backend's
        # (filepath, seed) tuples) — handle both shapes defensively.
        src = out[0] if isinstance(out, (list, tuple)) else out
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out_p)
        duration = _get_wav_duration(str(out_p))
        logger.info(f"HF Space VoxCPM2 saved: {out_p.name} ({duration:.1f}s)")
        return {"success": True, "audio_path": str(out_p), "duration_secs": duration, "error": ""}

    async def _hf_space_synthesize(
        self, text, voice_design, output_path, cfg_value,
        reference_audio_path, reference_transcript,
    ) -> dict:
        try:
            logger.info(f"HF Space VoxCPM2 TTS ({self.base_url}): {text[:50]}...")
            return await asyncio.to_thread(
                self._hf_space_call, text, voice_design, output_path, cfg_value,
                reference_audio_path, reference_transcript,
            )
        except Exception as e:
            logger.warning(f"HF Space VoxCPM2 failed: {e}")
            return {"success": False, "audio_path": "", "duration_secs": 0, "error": str(e)}

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
            resp = await self._get_http().post(url, json=payload, headers=headers, timeout=60.0)

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

        # "mock" lets callers with real backends configured treat this as a
        # failure (rate-limited end of the chain) instead of silently saving
        # a silent clip that LOOKS like a finished voice in the editor.
        return {"success": True, "audio_path": str(out_path), "duration_secs": duration, "error": "", "mock": True}

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
