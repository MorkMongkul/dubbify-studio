"""
voxcpm-modal/app.py
VoxCPM2 TTS server deployed on Modal — matches the contract that
Dubify's backend (app/services/tts_client.py) already expects:

    GET  /health  -> 200 {"status": "ok"}
    POST /tts     -> {"text", "cfg_value", "inference_timesteps"} -> WAV bytes

Scale-to-zero: the GPU container spins up on the first request and shuts
down after `scaledown_window` seconds idle, so it costs nothing while idle.

Deploy:
    pip install modal
    modal setup
    modal deploy app.py

After deploy, Modal prints a URL like:
    https://<workspace>--voxcpm2-web.modal.run
Put that (no trailing slash) into the backend .env as VOXCPM2_API_URL.
"""
import io
import os

import modal

# ── Model checkpoint ──────────────────────────────────────────────
# VoxCPM2 supports multilingual (incl. Khmer), voice design, and cloning.
# Override via the VOXCPM_MODEL_ID env var if needed.
MODEL_ID = os.environ.get("VOXCPM_MODEL_ID", "openbmb/VoxCPM2")
# Sample rate is read from the loaded model (model.tts_model.sample_rate),
# not hardcoded — see load().

# Where HuggingFace caches weights — baked into the image (see below).
CACHE_DIR = "/model-cache"


def _download_model():
    """
    Download the VoxCPM2 weights at IMAGE BUILD time (cheap CPU), not on the
    GPU at runtime. This is the single biggest cost saver on a tiny budget —
    the ~5–8 GB download never touches billed GPU seconds, and cold starts
    just load the already-present files.
    """
    from voxcpm import VoxCPM

    VoxCPM.from_pretrained(MODEL_ID, load_denoiser=False)


# ── Container image ───────────────────────────────────────────────
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git")
    .pip_install(
        "voxcpm",
        "torch>=2.5.0",          # VoxCPM2 requires PyTorch >= 2.5, CUDA >= 12
        "soundfile",
        "numpy",
        "fastapi[standard]",
    )
    .env({"HF_HOME": CACHE_DIR})
    .run_function(_download_model)   # bake weights into the image at build time
)

app = modal.App("voxcpm2")


@app.cls(
    image=image,
    # VoxCPM2 is 2B params, bf16, ~8GB VRAM. T4 (Turing) has no native bf16.
    # L4 (Ada, 24GB) supports bf16 and is the cheapest viable GPU — best for a
    # small budget. Bump to "A10G" later if you want a bit more speed.
    gpu="L4",
    scaledown_window=60,            # short idle → minimal wasted billing on $1
    timeout=600,                    # max 10 min per request
)
@modal.concurrent(max_inputs=4)     # a warm container can handle a few requests at once
class VoxCPM2:
    @modal.enter()
    def load(self):
        """Runs once per container start. Weights are already in the image."""
        from voxcpm import VoxCPM

        print(f"Loading VoxCPM model: {MODEL_ID}")
        self.model = VoxCPM.from_pretrained(MODEL_ID, load_denoiser=False)
        # Read the model's native output sample rate (don't hardcode it)
        self.sample_rate = self.model.tts_model.sample_rate
        print(f"Model loaded (sample_rate={self.sample_rate}).")

    @modal.asgi_app()
    def web(self):
        """The FastAPI app served on the public Modal URL."""
        import base64
        import tempfile
        from pathlib import Path

        from fastapi import FastAPI, Response, HTTPException, Header
        from pydantic import BaseModel
        import soundfile as sf

        # Optional bearer-token auth. Set an AUTH_TOKEN env var (or Modal secret)
        # to require it; leave unset for open testing. Must match VOXCPM2_API_KEY
        # in the Dubify backend if you enable it.
        expected_token = os.environ.get("AUTH_TOKEN", "")

        web_app = FastAPI(title="VoxCPM2 TTS")

        class TTSRequest(BaseModel):
            text: str
            cfg_value: float = 2.0
            inference_timesteps: int = 10
            # Optional cloning inputs (all three VoxCPM2 modes from one endpoint):
            #   - none                       -> Voice Design   (text carries "(desc)" prefix)
            #   - reference_audio_b64        -> Controllable Cloning (text may carry "(style)")
            #   - reference_audio_b64 + transcript -> Ultimate Cloning (control disabled)
            reference_audio_b64: str | None = None
            reference_transcript: str | None = None

        def _check_auth(authorization: str | None):
            if not expected_token:
                return
            if authorization != f"Bearer {expected_token}":
                raise HTTPException(status_code=401, detail="Invalid or missing token")

        @web_app.get("/health")
        def health():
            return {"status": "ok", "model": MODEL_ID}

        @web_app.post("/tts")
        def tts(req: TTSRequest, authorization: str | None = Header(default=None)):
            _check_auth(authorization)

            ref_path = None
            tmpdir = None
            try:
                # Materialise the reference clip to a temp file when cloning
                if req.reference_audio_b64:
                    tmpdir = tempfile.mkdtemp()
                    ref_path = str(Path(tmpdir) / "reference.wav")
                    Path(ref_path).write_bytes(base64.b64decode(req.reference_audio_b64))

                gen_kwargs = dict(
                    text=req.text,
                    cfg_value=req.cfg_value,
                    inference_timesteps=req.inference_timesteps,
                    normalize=True,
                )

                if ref_path and req.reference_transcript:
                    # Ultimate cloning: audio-continuation with transcript.
                    # Control instruction is disabled in this mode.
                    gen_kwargs.update(
                        prompt_wav_path=ref_path,
                        prompt_text=req.reference_transcript,
                        reference_wav_path=ref_path,
                    )
                elif ref_path:
                    # Controllable cloning: timbre from the clip, style from
                    # any "(style)" prefix already baked into text.
                    gen_kwargs.update(reference_wav_path=ref_path)
                # else: Voice Design — text carries the "(description)" prefix.

                wav = self.model.generate(**gen_kwargs)

                buf = io.BytesIO()
                sf.write(buf, wav, self.sample_rate, format="WAV")
                return Response(content=buf.getvalue(), media_type="audio/wav")
            finally:
                if tmpdir:
                    import shutil
                    shutil.rmtree(tmpdir, ignore_errors=True)

        return web_app
