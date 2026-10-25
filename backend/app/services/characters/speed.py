"""Is the face step fast enough on this Mac? On a slow one Movelo uses voices only.

The face step decodes the picture at 25 fps, finds faces with YuNet in every frame, reads the lips of each face with
Light-ASD and describes every 6th frame of a face with SFace. That is the expensive part of recognising characters (the
voice print is cheap), and on an older Mac it could take far longer than the dubbing itself. `face_step_enabled()` times
a small fixed workload once per app version, scales it to a 120 s episode and turns faces off when that would take more
than FACE_STEP_BUDGET_SECS. Only Intel Macs are measured: every Apple Silicon chip keeps faces on (the slowest, the M1,
took 153 s for a 120 s episode, and a fanless Mac's timings swing too much to decide on). The answer is kept in DUBIFY_DATA_DIR/character-speed.json (in the process only, in dev).

It runs the models for a few seconds on its first call, so call it in a worker thread, never on the event loop."""
import json
import logging
import os
import platform
import statistics
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.core.binaries import ffmpeg
from app.core.config import settings

logger = logging.getLogger(__name__)

FACE_STEP_BUDGET_SECS = 180   # for a 120 s episode
EPISODE_SECS = 120
FRAMES = EPISODE_SECS * 25
SPEECH_SHARE = 0.65           # the share of an episode inside lines (only those are read): above 9 of the owner's 11 episodes, mean 0.51
SPEECH_FRAMES = int(FRAMES * SPEECH_SHARE)   # decoded and run through YuNet
FACE_SHARE = 0.5              # the spike's mix: a face is on screen for half of the time...
FACES_ON_SCREEN = 1.5         # ...and then 1.5 of them
FACE_FRAMES = int(SPEECH_FRAMES * FACE_SHARE * FACES_ON_SCREEN)   # 1462: what Light-ASD reads
FEATURES = FACE_FRAMES // 6   # 375: SFace describes every 6th frame of a face (faces.FEATURE_EVERY)

CALIBRATION = 1.6             # real frames cost more than the synthetic ones (no faces: no crops or NMS; YuNet 35 ms vs 50 ms): M1 ep10 took 153 s, the bench said 92-101 s
SIZE = (720, 1280)            # the synthetic picture: a vertical episode, which costs the most
DECODE_FRAMES = 25
YUNET_RUNS = 3                # YuNet is the noisy part: the median of this many timings
ASD_FRAMES = 50
SFACE_CALLS = 5
VOICE_SECS = 5
CACHE_NAME = "character-speed.json"

_LOCK = threading.Lock()
_memo: dict | None = None     # the answer for this process (the only copy in dev)


@dataclass
class Parts:
    """Seconds each part of the step would take for a 120 s episode, from the timed workload."""
    decode: float
    yunet: float
    light_asd: float
    sface: float
    voice: float

    @property
    def faces(self) -> float:
        """What the face step costs. The voice print isn't in it: voices run on every Mac, with or without faces, so it
        can't decide between the two."""
        return self.decode + self.yunet + self.light_asd + self.sface

    @property
    def estimate(self) -> float:
        """The face step's calibrated time: what is compared with the budget."""
        return self.faces * CALIBRATION


def _timed(work, calls: int = 1) -> float:
    """Seconds `work` takes, after one untimed run that loads the model."""
    work()
    start = time.perf_counter()
    for _ in range(calls):
        work()
    return time.perf_counter() - start


def median_secs(work, runs: int) -> float:
    """The middle one of `runs` timings of `work`, in seconds: one slow run (the machine busy or hot) doesn't count."""
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        work()
        timings.append(time.perf_counter() - start)
    return statistics.median(timings)


def measure() -> Parts:
    """Times the synthetic workload. Raises (ModelMissing, ImportError, OSError…) when the models can't run here."""
    from app.services.characters import faces, lips, voiceprint

    width, height = SIZE
    frame_bytes = width * height * 3
    command = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
               f"testsrc2=size={width}x{height}:rate=25", "-frames:v", str(DECODE_FRAMES), "-f", "rawvideo",
               "-pix_fmt", "bgr24", "-"]   # the way faces.frames reads a video
    start = time.perf_counter()
    done = subprocess.run(command, capture_output=True)
    decode = time.perf_counter() - start
    if done.returncode != 0 or len(done.stdout) != DECODE_FRAMES * frame_bytes:
        raise RuntimeError(f"ffmpeg could not make the test picture: {done.stderr.decode(errors='replace')[-300:]}")
    pictures = [np.frombuffer(done.stdout, np.uint8, frame_bytes, k * frame_bytes).reshape(height, width, 3)
                for k in range(DECODE_FRAMES)]

    faces.detect(pictures[0])
    yunet = median_secs(lambda: [faces.detect(picture) for picture in pictures], YUNET_RUNS)

    rng = np.random.default_rng(0)
    sound = rng.integers(-3000, 3000, ASD_FRAMES * voiceprint.RATE // 25, dtype=np.int16)
    crops = rng.integers(0, 256, (ASD_FRAMES, 112, 112), dtype=np.uint8)
    light_asd = _timed(lambda: lips.speaking_scores(sound, crops))

    # a face the size of the 720-wide picture's middle, with its landmarks where a face has them
    face = np.array([210, 400, 300, 360, 290, 520, 430, 520, 360, 600, 310, 680, 410, 680, 0.9], np.float32)
    sface = _timed(lambda: faces.feature(pictures[0], face), SFACE_CALLS)

    voice = voiceprint.voice_print(rng.integers(-3000, 3000, VOICE_SECS * voiceprint.RATE, dtype=np.int16))
    start = time.perf_counter()
    voiceprint.voice_print(rng.integers(-3000, 3000, VOICE_SECS * voiceprint.RATE, dtype=np.int16))
    voice_secs = time.perf_counter() - start
    del voice

    return Parts(decode=decode * SPEECH_FRAMES / DECODE_FRAMES,
                 yunet=yunet * SPEECH_FRAMES / DECODE_FRAMES,
                 light_asd=light_asd * FACE_FRAMES / ASD_FRAMES,
                 sface=sface * FEATURES / SFACE_CALLS,
                 voice=voice_secs * EPISODE_SECS * SPEECH_SHARE / VOICE_SECS)


def _cache_path() -> Path | None:
    return Path(settings.DUBIFY_DATA_DIR) / CACHE_NAME if settings.DUBIFY_DATA_DIR else None


def _read_cache(version: str) -> dict | None:
    """The saved answer for this app version, or None (missing, unreadable or made by another version)."""
    path = _cache_path()
    if path is None or not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text())
        if saved["app_version"] == version and isinstance(saved["faces"], bool):
            float(saved["estimate_secs"])
            return saved
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning(f"{path.name} is unreadable — measuring again")
    return None


def _write_cache(answer: dict) -> None:
    path = _cache_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False) as f:
            json.dump(answer, f)
        os.replace(f.name, path)
    except OSError as e:   # the answer is only a saving of a few seconds
        logger.warning(f"Couldn't save {path.name}: {e}")


def face_step_enabled() -> bool:
    """True when the face step should run: its estimate for a 120 s episode is within FACE_STEP_BUDGET_SECS.

    Blocks for a few seconds the first time per app version. Raises when the measuring itself can't run (the models
    can't load): the caller then does what it does for any failure of the models."""
    global _memo
    if platform.machine() == "arm64":
        if _memo is None:
            logger.info("Face step: Apple Silicon, faces on")
            _memo = {"app_version": settings.version, "faces": True}
        return True
    version = settings.version
    with _LOCK:
        if _memo is not None and _memo["app_version"] == version:
            return _memo["faces"]
        answer = _read_cache(version)
        if answer is None:
            parts = measure()
            estimate = parts.estimate
            answer = {"app_version": version, "estimate_secs": round(estimate, 1),
                      "faces": estimate <= FACE_STEP_BUDGET_SECS,
                      "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            logger.info(f"Face step for a {EPISODE_SECS} s episode: about {estimate:.0f} s (calibrated; raw decode {parts.decode:.0f}, "
                        f"YuNet {parts.yunet:.0f}, Light-ASD {parts.light_asd:.0f}, SFace {parts.sface:.0f}; voices "
                        f"{parts.voice:.0f}); {'faces on' if answer['faces'] else 'voices only'}, budget "
                        f"{FACE_STEP_BUDGET_SECS} s")
            _write_cache(answer)
        else:
            logger.info(f"Face step: about {answer['estimate_secs']:.0f} s for a {EPISODE_SECS} s episode (saved); "
                        f"{'faces on' if answer['faces'] else 'voices only'}")
        _memo = answer
        return answer["faces"]
