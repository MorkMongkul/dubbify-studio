# VoxCPM2 on Modal

A scale-to-zero GPU server for VoxCPM2 TTS, matching the contract the Dubify
backend expects (`GET /health`, `POST /tts` → WAV bytes).

## 1. One-time setup

```bash
pip install modal      # in any Python env
modal setup            # opens a browser, links your free Modal account
```

## 2. Deploy

```bash
cd voxcpm-modal
modal deploy app.py
```

First deploy builds the image and (on the first request) downloads the model
weights into a persistent volume — so later cold starts are fast.

Modal prints a public URL like:

```
https://<your-workspace>--voxcpm2-web.modal.run
```

## 3. Point Dubify at it

In `backend/.env`:

```
VOXCPM2_API_URL=https://<your-workspace>--voxcpm2-web.modal.run
# VOXCPM2_API_KEY=   # only if you enabled AUTH_TOKEN (see below)
```

Restart the backend. Voice generation + voice design now use real VoxCPM2.

## 4. Test it directly (optional)

```bash
# health
curl https://<your-workspace>--voxcpm2-web.modal.run/health

# synthesize → saves out.wav
curl -X POST https://<your-workspace>--voxcpm2-web.modal.run/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"(calm adult male voice)សួស្តី តើអ្នកសុខសប្បាយទេ?","cfg_value":2.0,"inference_timesteps":10}' \
  --output out.wav
```

The first call is slow (~30–60s cold start + model load). Subsequent calls
while warm are fast. After 5 min idle the container scales to zero ($0).

## Notes

- **Model**: `openbmb/VoxCPM2` — 2B params, 30 languages (incl. Khmer), bf16,
  **48 kHz output**, ~8 GB VRAM. Loaded with `load_denoiser=False`; output
  sample rate is read from `model.tts_model.sample_rate` (= 48000).
- **GPU**: `L4` (Ada, 24 GB) — cheapest GPU that supports bf16, best for a small
  budget. Bump to `A10G` for a bit more speed. A T4 will **not** work well
  (Turing has no native bf16).
- **Voice design**: put the description in parentheses at the start of `text`,
  e.g. `"(A young woman, gentle and sweet voice)សួស្តី"`. This is exactly the
  format Dubify's client already sends.
- **Auth**: open by default for testing. To lock it down, set an `AUTH_TOKEN`
  (Modal secret) and put the same value in `VOXCPM2_API_KEY`.
- **Cost (tiny budget)**: weights are baked into the image at **build time**, so
  the big download never burns GPU seconds. With `gpu="L4"` (~$0.80/hr) and
  `scaledown_window=60`, a short test session (cold start + a handful of
  synth calls + ~1 min idle) costs roughly **$0.05–0.15**. A **$1 credit covers
  several test sessions.** Image builds are not billed as GPU time.
- **Cloning (later)**: VoxCPM2 supports `reference_wav_path` (basic/controllable)
  and `prompt_wav_path` + `prompt_text` (ultimate fidelity). Add these to the
  `/tts` handler + `model.generate(...)` for Option B.
- **Production**: the same FastAPI handler ports directly to an always-on
  RunPod/Vast.ai box (RTF ~0.3 on a 4090) when you're ready.
```
