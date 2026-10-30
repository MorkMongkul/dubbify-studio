"""Light-ASD (lip-sync) as ONNX and the MFCCs it needs, without PyTorch or scipy.

The fixtures are written by scripts/export-light-asd.py from the original torch model and
python_speech_features, so these tests compare against the reference implementations."""
from pathlib import Path

import numpy as np

from app.services.characters import lips
from app.services.characters.lips import speaking_scores
from app.services.characters.mfcc import mfcc

FIXTURES = Path(__file__).parent / "fixtures"


def test_mfcc_matches_the_reference_implementation():
    ref = np.load(FIXTURES / "mfcc_reference.npz")
    out = mfcc(ref["signal"])
    assert out.shape == ref["mfcc"].shape
    assert np.allclose(out, ref["mfcc"], atol=1e-3)


def test_light_asd_onnx_matches_torch():
    ref = np.load(FIXTURES / "lightasd_parity.npz")
    assert np.allclose(lips.run_onnx(ref["audio"], ref["video"]), ref["scores"], atol=1e-4)


def test_fewer_than_five_frames_give_no_scores():
    assert speaking_scores(np.zeros(16000, np.int16), np.zeros((4, 112, 112), np.uint8)) is None


def test_no_sound_gives_no_scores():
    """A face followed past the end of the vocals has no sound to judge (mfcc needs at least one sample)."""
    assert speaking_scores(np.zeros(0, np.int16), np.zeros((25, 112, 112), np.uint8)) is None


def test_speaking_scores_gives_a_score_per_frame_the_sound_covers():
    rng = np.random.default_rng(1)
    audio = (rng.standard_normal(16000 * 3) * 3000).astype(np.int16)
    crops = rng.integers(0, 256, (75, 112, 112), dtype=np.uint8)
    scores = speaking_scores(audio, crops)
    # 3 s of sound make 299 MFCC frames; the demo keeps a multiple of 4 (296 = 2.96 s), so 74 of the 75 frames
    assert scores is not None and scores.shape == (74,) and np.isfinite(scores).all()


def test_the_shipped_weights_judge_non_faces_as_not_speaking():
    """Random weights score near 0 and swing either way; the trained model says "not speaking" firmly for input
    that is no talking face (silence, noise, a speech-like tone against flat grey or noise pictures).
    This guards against an export that kept randomly-initialised weights. It cannot show that real speech is
    detected: that needs real footage, which the repo doesn't hold (measured on the owner's episodes in the spike)."""
    rng = np.random.default_rng(0)
    t = np.arange(32000) / 16000
    sounds = [
        np.zeros(32000, np.int16),
        (rng.standard_normal(32000) * 3000).astype(np.int16),
        (8000 * np.sin(2 * np.pi * 150 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t))).astype(np.int16),
    ]
    pictures = [rng.integers(0, 256, (50, 112, 112), dtype=np.uint8), np.full((50, 112, 112), 110, np.uint8)]
    for sound in sounds:
        for picture in pictures:
            assert speaking_scores(sound, picture).mean() < -2
