"""Faces without OpenCV: the numpy + onnxruntime port of FaceDetectorYN / FaceRecognizerSF and Light-ASD's crop, and the
frames the bundled ffmpeg hands them.

tests/fixtures/faces_parity.npz holds OpenCV 5.0.0's own answers on two drawn frames (a 720x1280 portrait and a
textured 360x240), written by scripts/export-yunet.py; the parity tests hold the port to them."""
import io
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.core.binaries import ffmpeg
from app.services.characters import faces, models

FIXTURES = Path(__file__).parent / "fixtures"
NEEDS_FETCH = "run scripts/fetch-models.sh"


def _ff(*args):
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _pictures():
    """(BGR frame, OpenCV's answers) for each fixture picture."""
    ref = np.load(FIXTURES / "faces_parity.npz")
    for key in ("a", "b"):
        rgb = np.asarray(Image.open(io.BytesIO(ref[f"{key}_png"].tobytes())).convert("RGB"))
        answers = {name: ref[f"{key}_{name}"] for name in ("faces", "matrices", "aligned", "features", "asd")}
        yield rgb[:, :, ::-1].copy(), answers


def test_yunet_parity_finds_the_same_faces_as_opencv():
    for frame, ref in _pictures():
        found = faces.detect(frame)
        assert found.shape == ref["faces"].shape and len(found) >= 2
        assert np.abs(found[:, :14] - ref["faces"][:, :14]).max() <= 1.0  # box and the five landmarks, in px
        assert np.abs(found[:, 14] - ref["faces"][:, 14]).max() <= 0.01  # score


def test_sface_parity_alignment_matrix_matches_opencv():
    for _, ref in _pictures():
        for face, matrix in zip(ref["faces"], ref["matrices"]):
            assert np.abs(faces.alignment_matrix(face) - matrix).max() < 1e-3


def test_sface_parity_aligned_face_matches_opencv():
    for frame, ref in _pictures():
        for face, aligned in zip(ref["faces"], ref["aligned"]):
            mine = faces.aligned_face(frame, face)
            assert mine.shape == (112, 112, 3) and mine.dtype == np.uint8
            assert np.abs(mine.astype(int) - aligned).mean() <= 1


def test_sface_parity_feature_matches_opencv():
    try:
        models.path("sface")
    except models.ModelMissing:
        pytest.skip(NEEDS_FETCH)
    for frame, ref in _pictures():
        for face, feature in zip(ref["faces"], ref["features"]):
            mine = faces.feature(frame, face)
            assert mine.shape == (128,)
            assert float(mine @ feature / np.linalg.norm(mine) / np.linalg.norm(feature)) >= 0.99


def test_light_asd_crop_parity_matches_opencv():
    for frame, ref in _pictures():
        for face, crop in zip(ref["faces"], ref["asd"]):
            mine = faces.asd_crop(frame, face)
            assert mine.shape == (112, 112) and mine.dtype == np.uint8
            assert np.abs(mine.astype(int) - crop).mean() <= 1


def test_frames_are_25_a_second_each_showing_the_picture_of_its_time(tmp_path):
    """A 30 fps video whose frame n is grey 4n: each frame given for time t is the source frame nearest t."""
    video = tmp_path / "count.mkv"
    _ff("-f", "lavfi", "-i", "nullsrc=s=64x48:r=30:d=2,format=rgb24,geq=r='4*N':g='4*N':b='4*N'",
        "-c:v", "ffv1", "-pix_fmt", "bgr0", str(video))
    for start in (0.0, 0.97, 1.01):
        got = list(faces.frames(str(video), start, start + 0.5))
        assert len(got) in (12, 13, 14)
        for k, (t, frame) in enumerate(got):
            assert t == pytest.approx(start + k / 25)
            assert frame.shape == (48, 64, 3) and frame.dtype == np.uint8
            shown = frame[0, 0, 0] / 4 / 30  # when the source frame shown was taken
            assert abs(shown - t) <= 1 / 30 + 1e-6


def test_frames_of_a_rotated_video_come_upright(tmp_path):
    plain, rotated = tmp_path / "plain.mp4", tmp_path / "rotated.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=s=64x48:r=25:d=1", "-c:v", "mpeg4", str(plain))
    _ff("-display_rotation", "90", "-i", str(plain), "-c", "copy", str(rotated))
    assert faces.video_size(str(rotated)) == (48, 64)
    _, frame = next(faces.frames(str(rotated), 0.0, 0.5))
    assert frame.shape == (64, 48, 3)


def test_a_file_without_a_picture_has_no_video_size(tmp_path):
    sound = tmp_path / "vocals.wav"
    _ff("-f", "lavfi", "-i", "sine=f=220:d=1", str(sound))
    assert faces.video_size(str(sound)) is None
