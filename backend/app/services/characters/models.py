"""The four models character recognition runs, and where they are.

`models.json` (next to the files in app/assets/models/) lists each one with its size and SHA-256. Two are committed
(YuNet, Light-ASD); the other two are too big for git and are fetched by scripts/fetch-models.sh, which the release
workflow runs before the backend is built. `app/assets` is bundled whole, so the files land in the packaged app.

This module imports no runtime library: path() works on a Mac that can't load onnxruntime or sherpa_onnx. Only
self_test() and the loaders that use those libraries import them, and recognition asks runtime_supported() first
(characters.store), so a Mac below macOS 13.4 never even tries. There is no OpenCV in the app (its macOS wheel bundles
a GPL FFmpeg): the two face models run as plain onnxruntime sessions."""
import json
import platform
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "assets" / "models"
MANIFEST = DIR / "models.json"

OK = "Character models: ok"
MIN_MACOS = (13, 4)   # onnxruntime 1.23.2's wheels (the app itself runs from macOS 11)


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
    """Where model `name` ("eres2netv2", "sface", "yunet", "lightasd") is; ModelMissing when the file isn't there."""
    entry = next((e for e in json.loads(MANIFEST.read_text()) if e["name"] == name), None)
    if entry is None:
        raise KeyError(f"unknown character model {name!r}")
    file = DIR / entry["file"]
    if not file.is_file():
        raise ModelMissing(f"{file.name} is missing — run scripts/fetch-models.sh")
    return file


def self_test() -> str:
    """Loads all four models and runs one tiny input through each. Returns "Character models: ok"."""
    import numpy as np
    import onnxruntime
    import sherpa_onnx

    from app.services.characters import lips

    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path("eres2netv2")), num_threads=1))
    stream = extractor.create_stream()
    stream.accept_waveform(16000, (np.random.default_rng(0).standard_normal(16000) * 0.1).astype(np.float32))
    stream.input_finished()
    if not extractor.is_ready(stream) or len(extractor.compute(stream)) != extractor.dim:
        raise RuntimeError("the voice model gave no voice print")

    def run(name: str, shape: tuple) -> list:
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        session = onnxruntime.InferenceSession(str(path(name)), options, providers=["CPUExecutionProvider"])
        return session.run(None, {session.get_inputs()[0].name: np.zeros(shape, np.float32)})

    # YuNet takes a picture of any size padded to multiples of 32 (here 384x256) and answers per anchor
    # (8/16/32 px strides: 48x32 + 24x16 + 12x8 of them).
    detections = run("yunet", (1, 3, 256, 384))
    if len(detections) != 12 or detections[0].shape != (1, 1536, 1) or detections[11].shape != (1, 96, 10):
        raise RuntimeError("the face detection model gave unexpected outputs")
    # SFace takes an aligned 112x112 face and gives a 128-number face print.
    face_print = run("sface", (1, 3, 112, 112))
    if face_print[0].shape != (1, 128):
        raise RuntimeError("the face model gave no face print")

    frames = 25
    scores = lips.run_onnx(np.zeros((1, frames * 4, 13), np.float32), np.zeros((1, frames, 112, 112), np.float32))
    if scores.shape[-1] != frames:
        raise RuntimeError("the lip-sync model gave the wrong number of scores")
    return OK
