"""The two voice models character recognition runs, and where they are.

`models.json` (next to the files in app/assets/models/) lists each one with its size and SHA-256. Both are too big for
git and are fetched by scripts/fetch-models.sh, which the release workflow runs before the backend is built.
`app/assets` is bundled whole, so the files land in the packaged app. The face models (YuNet, SFace, Light-ASD) went in
0.5.3: recognition has been by voice only since 0.5.2.

This module imports no runtime library: path() works on a Mac that can't load sherpa_onnx. Only self_test() and the
loaders that use it import it, and recognition asks runtime_supported() first (characters.store), so a Mac below
macOS 13.4 never even tries."""
import json
import platform
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "assets" / "models"
MANIFEST = DIR / "models.json"

OK = "Character models: ok"
MIN_MACOS = (13, 4)   # where the models have run (onnxruntime's wheels needed it until 0.5.3; sherpa-onnx's say 11.0,
                      # untried below 13.4; the app itself runs from macOS 11)
# How hard the models work (0.5.1, on the owner's fanless M1 MacBook Air, which a thread per core heated to 97 °C)
THREADS = 2


class ModelMissing(RuntimeError):
    pass


def runtime_supported() -> bool:
    """Whether this macOS can load the models' libraries (True off macOS). Asks the system only: nothing is imported."""
    release = platform.mac_ver()[0]
    try:
        version = tuple(int(part) for part in release.split(".")[:2]) if release else None
    except ValueError:
        return True
    return version is None or version + (0,) * (2 - len(version)) >= MIN_MACOS


def path(name: str) -> Path:
    """Where model `name` ("eres2netv2", "campplus") is; ModelMissing when the file isn't there."""
    entry = next((e for e in json.loads(MANIFEST.read_text()) if e["name"] == name), None)
    if entry is None:
        raise KeyError(f"unknown character model {name!r}")
    file = DIR / entry["file"]
    if not file.is_file():
        raise ModelMissing(f"{file.name} is missing — run scripts/fetch-models.sh")
    return file


def self_test() -> str:
    """Loads both voice models and prints one second of noise with each. Returns "Character models: ok"."""
    import numpy as np
    import sherpa_onnx

    noise = (np.random.default_rng(0).standard_normal(16000) * 0.1).astype(np.float32)
    for name in ("eres2netv2", "campplus"):
        extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path(name)), num_threads=THREADS))
        stream = extractor.create_stream()
        stream.accept_waveform(16000, noise)
        stream.input_finished()
        if not extractor.is_ready(stream) or len(extractor.compute(stream)) != 192:
            raise RuntimeError(f"the voice model {name} gave no voice print")
    return OK
