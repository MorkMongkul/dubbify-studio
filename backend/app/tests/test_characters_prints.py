"""Prints for a line: its voice print, and the face that speaks it (lip-sync) with that face's print and a thumbnail.

All media is made here with ffmpeg (nothing real). The tracking test stubs the face detector, the face model and the
lip scorer, so it checks only how faces are followed and which one is chosen."""
import io
import subprocess
import tracemalloc
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.core.binaries import ffmpeg
from app.services.characters import faces, lips, models, voiceprint
from app.services.characters.prints import LinePrints, prints_for_spans

FIXTURES = Path(__file__).parent / "fixtures"
NEEDS_FETCH = "run scripts/fetch-models.sh"


def _ff(*args):
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _need(*names):
    for name in names:
        try:
            models.path(name)
        except models.ModelMissing:
            pytest.skip(NEEDS_FETCH)


@pytest.fixture
def media(tmp_path):
    """3 s of testsrc2 (no faces) and stereo 44.1 kHz vocals (as separation writes them): a 2 s tone from 0.5 s."""
    video, vocals = tmp_path / "source.mp4", tmp_path / "vocals.wav"
    _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=3", "-c:v", "mpeg4", str(video))
    _ff("-f", "lavfi", "-i", "sine=f=220:d=2", "-af", "adelay=500:all=1,apad=whole_dur=3", "-ac", "2", "-ar", "44100",
        str(vocals))
    return str(video), str(vocals)


def test_a_video_with_no_faces_gives_voice_only(media):
    _need("eres2netv2")
    video, vocals = media
    [p] = prints_for_spans(video, vocals, [(0.5, 2.5)])
    assert p.voice is not None and p.voice.shape == (192,) and abs(np.linalg.norm(p.voice) - 1) < 1e-5
    assert p.face is None and p.speaking_score is None and p.face_crop is None


def test_a_line_shorter_than_0_3_s_has_no_voice_print(media):
    video, vocals = media
    assert prints_for_spans(video, vocals, [(1.0, 1.25)])[0].voice is None


def test_no_vocals_file_gives_no_voice_print(media):
    video, _ = media
    assert prints_for_spans(video, None, [(0.5, 2.5)]) == [LinePrints(None, None, None, None)]


def test_with_faces_off_the_picture_is_not_read(media, monkeypatch):
    _need("eres2netv2")
    video, vocals = media
    monkeypatch.setattr(faces, "video_size", lambda *a, **k: pytest.fail("the picture was read"))
    monkeypatch.setattr(faces, "frames", lambda *a, **k: pytest.fail("the picture was read"))
    [p] = prints_for_spans(video, vocals, [(0.5, 2.5)], with_faces=False)
    assert p.voice is not None and p.face is None and p.speaking_score is None


def _face(x, y, side, score):
    face = np.zeros(15, np.float32)
    face[:4] = (x, y, side, side)
    face[14] = score
    return face


def test_faces_are_followed_by_overlap_and_the_most_speaking_track_wins(monkeypatch):
    w, h, n = 640, 360, 50  # 2 s at 25 fps
    picture = np.zeros((h, w, 3), np.uint8)
    picture[:, w // 2:] = 220  # faces on the right are bright: that is how the stub lip scorer tells the sides apart
    found: list[list[np.ndarray]] = []
    b_faces = []
    for k in range(n):
        here = []
        if k < 40 and not 10 <= k <= 12:  # A (left): missing 0.16 s, so two tracks of 10 and 27 frames
            here.append(_face(100 + k % 3, 120, 60, 0.9))
        if 5 <= k < 35 and k != 15:  # B (right): one missed frame (0.08 s) is bridged, 29 frames found, 30 crops
            here.append(_face(450 + k % 2, 120, 60, 0.8 + k / 1000))
            b_faces.append(here[-1])
        if k == 20:  # a second box on B in the same frame starts a track of its own
            here.append(_face(452, 122, 60, 0.75))
        if 40 <= k < 48:  # C (right): would speak, but 8 frames are too few to judge
            here.append(_face(450, 250, 60, 0.95))
        here.append(_face(300, 300, 30, 0.99))  # under 40 px: never followed
        found.append(here)

    def frames(video_path, start, end, fps=25, *, size=None):
        assert size == (w, h)
        for k in range(n):
            yield start + k / 25, picture

    monkeypatch.setattr(faces, "video_size", lambda path: (w, h))
    monkeypatch.setattr(faces, "frames", frames)

    def feature(frame, face):
        v = np.zeros(128, np.float32)
        v[int(face[0]) % 100] = 3.0
        v[127] = face[14] * 10
        return v

    def silence(path, start, end):
        return np.zeros(round((end - start) * 16000), np.int16)

    monkeypatch.setattr(faces, "feature", feature)
    monkeypatch.setattr(voiceprint, "read_audio", silence)
    monkeypatch.setattr(voiceprint, "voice_print", lambda audio: None)

    def run(left: float, right: float):
        """Prints for the line when the lip scorer gives faces on the left `left` and on the right `right`."""
        calls, judged = iter(found), []
        monkeypatch.setattr(faces, "detect", lambda frame: np.array(next(calls)))

        def speaking_scores(audio, crops):
            judged.append((len(audio), len(crops)))
            return np.full(len(crops), right if crops.mean() > 150 else left)

        monkeypatch.setattr(lips, "speaking_scores", speaking_scores)
        [p] = prints_for_spans("video.mp4", "vocals.wav", [(3.0, 5.0)])
        return p, judged

    p, judged = run(left=0.5, right=1.5)
    # A's two tracks and B are judged, each on the sound from its first frame to the end of its last (640 samples each)
    # with a crop for every one of those frames
    assert sorted(judged) == sorted([(10 * 640, 10), (27 * 640, 27), (30 * 640, 30)])
    assert p.speaking_score == pytest.approx(1.5)
    sampled = [feature(None, f) for f in b_faces[::6]]  # every 6th frame of B, from its first
    expected = np.mean([v / np.linalg.norm(v) for v in sampled], axis=0)
    expected /= np.linalg.norm(expected)
    assert p.face.shape == (128,) and np.allclose(p.face, expected, atol=1e-6)
    thumb = Image.open(io.BytesIO(p.face_crop))
    assert thumb.format == "JPEG" and max(thumb.size) <= 160 and np.asarray(thumb).mean() > 150

    p, _ = run(left=-0.5, right=0.0)  # nobody's mean is above 0: the speaker is off screen
    assert p == LinePrints(None, None, None, None)


def test_a_missed_frame_is_cropped_at_the_last_box_so_crops_keep_step_with_the_sound(monkeypatch):
    """A face missed on one frame mid-track still gets a crop for that frame: that frame's pixels at the track's last
    box. Without it every later crop would be judged against sound 40 ms early."""
    w, h, n = 320, 240, 20
    rng = np.random.default_rng(4)
    pictures = [rng.integers(0, 256, (h, w, 3), dtype=np.uint8) for _ in range(n)]
    boxes = {k: _face(100 + 2 * k, 80, 60, 0.9) for k in range(2, 16) if k != 8}  # missed on frame 8
    judged = []

    def frames(video_path, start, end, fps=25, *, size=None):
        for k in range(n):
            yield start + k / 25, pictures[k]

    def detect(frame):
        k = next(i for i, picture in enumerate(pictures) if picture is frame)
        return np.array([boxes[k]] if k in boxes else np.zeros((0, 15), np.float32))

    def speaking_scores(audio, crops):
        judged.append((audio, crops))
        return np.ones(len(crops))

    monkeypatch.setattr(faces, "video_size", lambda path: (w, h))
    monkeypatch.setattr(faces, "frames", frames)
    monkeypatch.setattr(faces, "detect", detect)
    monkeypatch.setattr(faces, "feature", lambda frame, face: np.ones(128, np.float32))
    monkeypatch.setattr(lips, "speaking_scores", speaking_scores)
    monkeypatch.setattr(voiceprint, "read_audio", lambda path, start, end: np.zeros(round(end * 16000), np.int16))
    monkeypatch.setattr(voiceprint, "voice_print", lambda audio: None)

    [p] = prints_for_spans("video.mp4", "vocals.wav", [(0.0, n / 25)])
    [(audio, crops)] = judged
    first, last = 2, 15
    assert len(crops) == last - first + 1 and len(audio) == (last - first + 1) * 640
    for k in range(first, last + 1):
        box = boxes.get(k, boxes.get(k - 1))  # frame 8 at frame 7's box
        assert np.array_equal(crops[k - first], faces.asd_crop(pictures[k], box)), k
    assert not np.array_equal(crops[8 - first], faces.asd_crop(pictures[7], boxes[7]))
    assert p.speaking_score == pytest.approx(1.0)


def test_a_face_judged_speaking_gives_its_face_print_and_a_thumbnail(tmp_path, monkeypatch):
    """The real path (bundled-ffmpeg frames, YuNet, tracking, SFace) on a still drawn frame; only lip-sync is stubbed,
    saying the fixture's first face speaks. Its print must be OpenCV's feature for that face."""
    _need("eres2netv2", "sface")
    ref = np.load(FIXTURES / "faces_parity.npz")
    still, video, vocals = tmp_path / "a.png", tmp_path / "still.mkv", tmp_path / "vocals.wav"
    still.write_bytes(ref["a_png"].tobytes())
    _ff("-loop", "1", "-framerate", "25", "-i", str(still), "-t", "1", "-c:v", "ffv1", "-pix_fmt", "bgr0", str(video))
    _ff("-f", "lavfi", "-i", "sine=f=220:d=1", str(vocals))
    speaker = ref["a_asd"][0]

    def speaking_scores(audio, crops):
        return np.full(len(crops), 2.0 if np.abs(crops[0].astype(int) - speaker).mean() < 5 else -1.0)

    monkeypatch.setattr(lips, "speaking_scores", speaking_scores)
    [p] = prints_for_spans(str(video), str(vocals), [(0.0, 0.8)])
    assert p.speaking_score == pytest.approx(2.0)
    assert p.face.shape == (128,) and abs(np.linalg.norm(p.face) - 1) < 1e-5
    feature = ref["a_features"][0]
    assert float(p.face @ feature) / np.linalg.norm(feature) >= 0.99
    thumb = Image.open(io.BytesIO(p.face_crop))
    assert thumb.format == "JPEG" and 0 < max(thumb.size) <= 160


def test_prints_keep_no_frames(tmp_path, monkeypatch):
    """Review Focus 1: frames are used and let go one at a time (60 s of 720x1280 is 4 GB of frames), while a face is
    followed through every line: crops, a thumbnail and the voice prints are real. The detector, SFace and lip-sync
    are stubbed (onnxruntime's own memory isn't seen by tracemalloc, and YuNet over 1500 frames is slow)."""
    _need("eres2netv2")
    video, vocals = tmp_path / "long.mp4", tmp_path / "vocals.wav"
    _ff("-f", "lavfi", "-i", "testsrc2=s=720x1280:r=25:d=60", "-c:v", "mpeg4", "-q:v", "10", str(video))
    _ff("-f", "lavfi", "-i", "sine=f=220:d=60", "-ac", "2", "-ar", "44100", str(vocals))
    seen = []
    face = _face(240, 520, 240, 0.9)
    monkeypatch.setattr(faces, "detect", lambda frame: seen.append(frame.shape) or face[None])
    monkeypatch.setattr(faces, "feature", lambda frame, f: np.full(128, 128 ** -0.5, np.float32))
    monkeypatch.setattr(lips, "speaking_scores", lambda audio, crops: np.full(len(crops), 1.0))
    spans = [(i * 5.0, i * 5.0 + 5.0) for i in range(12)]
    tracemalloc.start()
    try:
        result = prints_for_spans(str(video), str(vocals), spans)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(result) == 12 and all(p.voice is not None and p.face is not None and p.face_crop for p in result)
    assert len(seen) >= 1490 and seen[0] == (1280, 720, 3)
    assert peak < 300 * 1024 ** 2
