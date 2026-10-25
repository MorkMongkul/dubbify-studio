"""Faces in the picture: where they are (YuNet), whose they are (SFace), and the crop Light-ASD reads the lips from.

A numpy + onnxruntime port of OpenCV 5.0.0's FaceDetectorYN and FaceRecognizerSF (objdetect/src/face_detect.cpp,
face_recognize.cpp) and of the Light-ASD demo's crop: the app ships no OpenCV, whose macOS wheel bundles a GPL FFmpeg.
tests/fixtures/faces_parity.npz (OpenCV's own answers, written by scripts/export-yunet.py) is the check. Frames come
from the bundled ffmpeg one at a time, and a track keeps only crops and features, never frames."""
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator

import numpy as np
from PIL import Image

from app.core.binaries import ffmpeg, ffprobe
from app.services.characters import models

FPS = 25
SCORE = 0.7
MIN_SIDE = 40
NMS_IOU = 0.3
TOP_K = 5000
STRIDES = (8, 16, 32)
OUTPUTS = [f"{kind}_{stride}" for kind in ("cls", "obj", "bbox", "kps") for stride in STRIDES]
# Where SFace's 112x112 input wants the eyes, nose tip and mouth corners (face_recognize.cpp; its mean is rounded there)
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655],
                     [70.7299, 92.2041]], np.float32)
TEMPLATE_MEAN = np.array([56.0262, 71.9008], np.float32)
CROP_PAD = 0.40
CROP_GREY = 110
TRACK_IOU = 0.3
TRACK_GAP = 0.09  # s: two missed frames at 25 fps
FEATURE_EVERY = 6
THUMBNAIL = 160


@lru_cache(maxsize=2)
def _session(name: str):
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(str(models.path(name)), options, providers=["CPUExecutionProvider"])


def video_size(video_path: str) -> tuple[int, int] | None:
    """(width, height) of the frames ffmpeg decodes (it turns a rotated video upright), or None without a picture."""
    result = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=width,height:stream_side_data=rotation", "-of", "json", video_path],
                            capture_output=True, text=True)
    try:
        stream = json.loads(result.stdout)["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError):
        return None
    if width <= 0 or height <= 0:
        return None
    rotation = next((int(float(d["rotation"])) for d in stream.get("side_data_list", []) if "rotation" in d), 0)
    return (height, width) if rotation % 180 else (width, height)


def frames(video_path: str, start: float, end: float, fps: int = FPS, *,
           size: tuple[int, int] | None = None) -> Iterator[tuple[float, np.ndarray]]:
    """The picture from `start` to `end` s at `fps`, one (time, BGR uint8 frame) at a time: frame k is start + k/fps.

    start_time=0 makes the fps filter count from `start`; without it, it counts from the first frame after `start` and
    labels every frame one late. `size` saves a probe when the caller knows it (video_size)."""
    size = size or video_size(video_path)
    if size is None or end <= start:
        return
    width, height = size
    frame_bytes = width * height * 3
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start:.6f}", "-to", f"{end:.6f}",
             "-i", video_path, "-map", "0:v:0", "-vf", f"fps={fps}:start_time=0", "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-"], stdout=subprocess.PIPE, stderr=errors)
        read_all = False
        try:
            k = 0
            while len(data := process.stdout.read(frame_bytes)) == frame_bytes:
                yield start + k / fps, np.frombuffer(data, np.uint8).reshape(height, width, 3)
                k += 1
            read_all = True
        finally:
            if not read_all:  # the caller stopped early
                process.kill()
            process.stdout.close()
            code = process.wait()
        if code != 0:
            errors.seek(0)
            raise RuntimeError(f"ffmpeg could not read {video_path}: {errors.read().decode(errors='replace')[-300:]}")


def iou(a: np.ndarray, b: np.ndarray) -> float:
    """Overlap of two (x, y, w, h) boxes: intersection over union."""
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = float(ix * iy)
    union = float(a[2] * a[3] + b[2] * b[3]) - inter
    return inter / union if union > 0 else 1.0


def detect(frame: np.ndarray, threshold: float = SCORE) -> np.ndarray:
    """FaceDetectorYN.detect at the frame's own size: rows of (x, y, w, h, right eye, left eye, nose tip, right and
    left mouth corners as x, y pairs, score), float32 (N, 15), best first."""
    h, w = frame.shape[:2]
    pad_h, pad_w = (h - 1) // 32 * 32 + 32, (w - 1) // 32 * 32 + 32  # padded bottom/right with black
    blob = np.zeros((1, 3, pad_h, pad_w), np.float32)
    blob[0, :, :h, :w] = frame.transpose(2, 0, 1)
    out = dict(zip(OUTPUTS, _session("yunet").run(OUTPUTS, {"input": blob})))
    found = []
    for stride in STRIDES:
        score = np.sqrt(np.clip(out[f"cls_{stride}"][0, :, 0], 0, 1) * np.clip(out[f"obj_{stride}"][0, :, 0], 0, 1))
        keep = np.flatnonzero(score >= threshold)
        row = (keep // (pad_w // stride)).astype(np.float32)[:, None]
        col = (keep % (pad_w // stride)).astype(np.float32)[:, None]
        box, points = out[f"bbox_{stride}"][0, keep], out[f"kps_{stride}"][0, keep]
        size = np.exp(box[:, 2:4]) * stride
        faces = np.empty((len(keep), 15), np.float32)
        faces[:, 0:1] = (col + box[:, 0:1]) * stride - size[:, 0:1] / 2
        faces[:, 1:2] = (row + box[:, 1:2]) * stride - size[:, 1:2] / 2
        faces[:, 2:4] = size
        faces[:, 4:14:2] = (points[:, 0::2] + col) * stride
        faces[:, 5:14:2] = (points[:, 1::2] + row) * stride
        faces[:, 14] = score[keep]
        found.append(faces)
    faces = np.concatenate(found)
    return _nms(faces, threshold) if len(faces) > 1 else faces


def _nms(faces: np.ndarray, threshold: float) -> np.ndarray:
    """dnn::NMSBoxes as FaceDetectorYN calls it: integer boxes, scores strictly above the threshold, best first."""
    boxes = faces[:, :4].astype(np.int32)
    order = [i for i in np.argsort(-faces[:, 14], kind="stable") if faces[i, 14] > threshold][:TOP_K]
    kept: list[int] = []
    for i in order:
        if all(iou(boxes[i], boxes[j]) <= NMS_IOU for j in kept):
            kept.append(i)
    return faces[kept]


def alignment_matrix(face: np.ndarray) -> np.ndarray:
    """FaceRecognizerSF's similarity transform (Umeyama) taking the face's five landmarks onto TEMPLATE: (2, 3)."""
    src = np.asarray(face[4:14], np.float32).reshape(5, 2)
    src_mean = src.sum(axis=0) / 5
    src_d = (src - src_mean).astype(np.float64)
    a = (TEMPLATE - TEMPLATE_MEAN).astype(np.float64).T @ src_d / 5
    d = np.array([1.0, -1.0 if np.linalg.det(a) < 0 else 1.0])
    u, s, vt = np.linalg.svd(a)
    rank = int((s > s.max() * 2 * np.finfo(np.float32).tiny).sum())
    if rank == 1:
        rotation = u @ vt if np.linalg.det(u) * np.linalg.det(vt) > 0 else u @ np.diag([1.0, -1.0]) @ vt
    else:
        rotation = u @ np.diag(d) @ vt
    scale = (s @ d) / ((src_d ** 2).sum() / 5)
    shift = TEMPLATE_MEAN - scale * (rotation @ src_mean.astype(np.float64))
    return np.hstack([rotation * scale, shift[:, None]])


def _warp_affine(frame: np.ndarray, matrix: np.ndarray, size: int = 112) -> np.ndarray:
    """cv2.warpAffine with INTER_LINEAR and a black border: each output pixel samples the frame bilinearly at the
    inverse map of its position, rounded to 1/32 px as OpenCV's fixed-point positions are."""
    m = matrix
    det = m[0, 0] * m[1, 1] - m[0, 1] * m[1, 0]
    det = 1 / det if det else 0.0  # cv2.invertAffineTransform: a singular map samples one point
    linear = np.array([[m[1, 1], -m[0, 1]], [-m[1, 0], m[0, 0]]]) * det
    inverse = np.hstack([linear, -linear @ m[:, 2:]])
    v, u = np.mgrid[0:size, 0:size]
    sx = np.round((inverse[0, 0] * u + inverse[0, 1] * v + inverse[0, 2]) * 32) / 32
    sy = np.round((inverse[1, 0] * u + inverse[1, 1] * v + inverse[1, 2]) * 32) / 32
    x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
    h, w = frame.shape[:2]
    out = np.zeros((size, size, frame.shape[2]), np.float64)
    for dy, dx, weight in ((0, 0, (1 - fx) * (1 - fy)), (0, 1, fx * (1 - fy)), (1, 0, (1 - fx) * fy), (1, 1, fx * fy)):
        y, x = y0 + dy, x0 + dx
        inside = ((y >= 0) & (y < h) & (x >= 0) & (x < w))[..., None]
        out += np.where(inside, frame[y.clip(0, h - 1), x.clip(0, w - 1)], 0) * weight
    return np.floor(out + 0.5).clip(0, 255).astype(np.uint8)


def aligned_face(frame: np.ndarray, face: np.ndarray) -> np.ndarray:
    """FaceRecognizerSF.alignCrop: the face turned and scaled onto SFace's 112x112 template (BGR)."""
    return _warp_affine(frame, alignment_matrix(face))


def feature(frame: np.ndarray, face: np.ndarray) -> np.ndarray:
    """FaceRecognizerSF.feature(alignCrop(frame, face)): SFace's 128 numbers for the face, not normalised."""
    blob = aligned_face(frame, face)[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32)  # RGB, 0-255
    return _session("sface").run(None, {"data": blob})[0][0]


def _linear_taps(src: int, dst: int, first: int, count: int):
    """cv2.resize INTER_LINEAR along one axis: for outputs first..first+count-1, the two source indices and the weight
    of the second (edges clamp)."""
    pos = (np.arange(first, first + count) + 0.5) * (src / dst) - 0.5
    low = np.floor(pos).astype(np.int64)
    weight = pos - low
    weight[low < 0], low[low < 0] = 0, 0
    weight[low >= src - 1], low[low >= src - 1] = 0, src - 1
    return low, np.minimum(low + 1, src - 1), weight


def asd_crop(frame: np.ndarray, face: np.ndarray) -> np.ndarray:
    """Light-ASD's view of a face, as its demo makes it: the box padded by 40% (grey 110 beyond the frame), resized to
    224x224, grey, the centre 112x112. Only the part of the padded frame that's used is built."""
    bs = max(face[2], face[3]) / 2
    cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
    pad = int(bs * (1 + 2 * CROP_PAD))
    h, w = frame.shape[:2]
    my, mx = cy + pad, cx + pad
    # numpy slicing of the padded frame, as the demo's img[a:b, c:d]
    rows = range(*slice(int(my - bs), int(my + bs * (1 + 2 * CROP_PAD))).indices(h + 2 * pad))
    cols = range(*slice(int(mx - bs * (1 + CROP_PAD)), int(mx + bs * (1 + CROP_PAD))).indices(w + 2 * pad))
    if not rows or not cols:
        return np.full((112, 112), CROP_GREY, np.uint8)
    region = np.full((len(rows), len(cols), 3), CROP_GREY, np.uint8)
    r0, r1 = max(rows.start, pad), min(rows.stop, h + pad)
    c0, c1 = max(cols.start, pad), min(cols.stop, w + pad)
    if r0 < r1 and c0 < c1:
        inside = frame[r0 - pad:r1 - pad, c0 - pad:c1 - pad]
        region[r0 - rows.start:r1 - rows.start, c0 - cols.start:c1 - cols.start] = inside
    y0, y1, fy = _linear_taps(len(rows), 224, 56, 112)
    x0, x1, fx = _linear_taps(len(cols), 224, 56, 112)
    band = region[y0] * (1 - fy)[:, None, None] + region[y1] * fy[:, None, None]  # only the 112 rows used, as floats
    bgr = np.floor(band[:, x0] * (1 - fx)[None, :, None] + band[:, x1] * fx[None, :, None] + 0.5).astype(np.int32)
    # cv2.COLOR_BGR2GRAY on 8-bit: fixed-point 0.114 B + 0.587 G + 0.299 R
    return ((bgr[..., 0] * 1868 + bgr[..., 1] * 9617 + bgr[..., 2] * 4899 + 8192) >> 14).astype(np.uint8)


def thumbnail(frame: np.ndarray, face: np.ndarray) -> Image.Image | None:
    """The face with a fifth of its size around it, RGB, long side <= 160 px."""
    margin = 0.2 * max(face[2], face[3])
    h, w = frame.shape[:2]
    x0, y0 = max(int(face[0] - margin), 0), max(int(face[1] - margin), 0)
    x1, y1 = min(int(face[0] + face[2] + margin), w), min(int(face[1] + face[3] + margin), h)
    if x1 <= x0 or y1 <= y0:
        return None
    picture = Image.fromarray(np.ascontiguousarray(frame[y0:y1, x0:x1, ::-1]))
    picture.thumbnail((THUMBNAIL, THUMBNAIL))
    return picture


@dataclass
class Track:
    """One face followed through a line: per frame its box and Light-ASD crop, SFace features every 6th frame
    (L2-normalised), and a thumbnail of its best-scored detection. Never a frame."""
    frames: list[int] = field(default_factory=list)  # frame numbers from the line's start
    times: list[float] = field(default_factory=list)
    boxes: list[np.ndarray] = field(default_factory=list)
    crops: list[np.ndarray] = field(default_factory=list)
    features: list[np.ndarray] = field(default_factory=list)
    best_score: float = -1.0
    picture: Image.Image | None = None

    def add(self, k: int, t: float, frame: np.ndarray, face: np.ndarray) -> None:
        self.frames.append(k)
        self.times.append(t)
        self.boxes.append(face[:4].copy())
        self.crops.append(asd_crop(frame, face))
        if len(self.frames) % FEATURE_EVERY == 1:
            vector = feature(frame, face)
            self.features.append(vector / np.linalg.norm(vector))
        if face[14] > self.best_score:
            self.best_score, self.picture = float(face[14]), thumbnail(frame, face)


def follow(tracks: list[Track], t: float, face: np.ndarray) -> Track:
    """The track this face continues: of those seen <= 0.09 s ago, the one whose last box overlaps it most, if by
    IoU >= 0.3 and it has no face at this time yet; else a new one (added to `tracks`)."""
    recent = [track for track in tracks if t - track.times[-1] <= TRACK_GAP]
    best = max(recent, key=lambda track: iou(track.boxes[-1], face), default=None)
    if best is None or iou(best.boxes[-1], face) < TRACK_IOU or best.times[-1] == t:
        best = Track()
        tracks.append(best)
    return best
