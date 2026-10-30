#!/usr/bin/env python3
"""Dev-only: YuNet (face detection) for any picture size, and the OpenCV fixtures the app's face code is tested with.

opencv_zoo's face_detection_yunet_2023mar.onnx declares a fixed [1, 3, 640, 640] input, which onnxruntime enforces.
OpenCV's FaceDetectorYN runs it at the video's own size (padded bottom/right to a multiple of 32); its graph only
reshapes to [1, -1, k] and upsamples by a scale of 2, so the same weights take any such size once the input's height
and width are symbolic. This script rewrites those two dims (and the outputs' anchor counts), drops the stale
intermediate shapes, and checks the result: identical to the original at 640x640, and the right shapes at non-square
sizes. Faces are judged at their own size that way (letterboxing a 720x1280 frame to 640 would halve them).

Run once with a Python that has onnx, onnxruntime, numpy and OpenCV 5.0.0 (opencv-python), never the app's:

  curl -LO https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
  scripts/fetch-models.sh   # SFace, for the fixtures
  python scripts/export-yunet.py face_detection_yunet_2023mar.onnx

Commit only the outputs:
  backend/app/assets/models/face_detection_yunet_2023mar_dynamic.onnx   (then update models.json: file, sha256, bytes)
  backend/app/tests/fixtures/faces_parity.npz

The fixtures are OpenCV's own answers on two drawn (synthetic) frames: FaceDetectorYN's faces (score >= 0.7),
FaceRecognizerSF's alignment matrix (recovered from alignCrop on a picture whose pixels hold their own coordinates),
aligned crop and feature for each face, and Light-ASD's face crop exactly as the spike made it (cv2.copyMakeBorder,
cv2.resize, cv2.cvtColor). The app has no OpenCV: app/services/characters/faces.py is a numpy port of these.
"""
import hashlib
import sys
from pathlib import Path

import cv2
import numpy as np
import onnx
import onnxruntime

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "backend/app/assets/models"
FIXTURES = ROOT / "backend/app/tests/fixtures"
ORIGINAL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
STRIDES = (8, 16, 32)
SCORE = 0.7
CS = 0.40  # Light-ASD's crop padding

assert cv2.__version__.startswith("5.0.0"), f"the fixtures are made with OpenCV 5.0.0, not {cv2.__version__}"
source = Path(sys.argv[1] if len(sys.argv) > 1 else "face_detection_yunet_2023mar.onnx").resolve()
assert hashlib.sha256(source.read_bytes()).hexdigest() == ORIGINAL_SHA256, f"{source} is not opencv_zoo's 2023mar YuNet"

# ── the model ────────────────────────────────────────────────────────────────────────────────────────────────────────
model = onnx.load(str(source))
graph = model.graph
for node in graph.node:  # every reshape keeps the anchor axis free, so nothing in the graph pins the size
    if node.op_type == "Reshape":
        shape = next(onnx.numpy_helper.to_array(t) for t in graph.initializer if t.name == node.input[1])
        assert shape[1] == -1, (node.name, shape)
dims = graph.input[0].type.tensor_type.shape.dim
assert [d.dim_value for d in dims] == [1, 3, 640, 640]
dims[2].dim_param, dims[3].dim_param = "height", "width"
for output in graph.output:
    stride = output.name.split("_")[1]
    output.type.tensor_type.shape.dim[1].dim_param = f"anchors_{stride}"
del graph.value_info[:]  # intermediate shapes recorded for 640x640
onnx.checker.check_model(model)
target = MODELS / "face_detection_yunet_2023mar_dynamic.onnx"
onnx.save(model, str(target))


def session(path: Path):
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


original, rewritten = session(source), session(target)
rng = np.random.default_rng(3)
blob = rng.uniform(0, 255, (1, 3, 640, 640)).astype(np.float32)
for a, b in zip(original.run(None, {"input": blob}), rewritten.run(None, {"input": blob})):
    assert np.array_equal(a, b), "the rewritten model must give exactly the original's answers at 640x640"
for h, w in ((1280, 736), (736, 1280), (352, 512)):
    outputs = rewritten.run(None, {"input": rng.uniform(0, 255, (1, 3, h, w)).astype(np.float32)})
    for out, meta in zip(outputs, rewritten.get_outputs()):
        kind, stride = meta.name.split("_")
        stride, width = int(stride), {"cls": 1, "obj": 1, "bbox": 4, "kps": 10}[kind]
        assert out.shape == (1, (h // stride) * (w // stride), width), (meta.name, out.shape, h, w)
print(f"{target.name}: {target.stat().st_size} bytes, sha256 {hashlib.sha256(target.read_bytes()).hexdigest()}")


# ── the fixtures ─────────────────────────────────────────────────────────────────────────────────────────────────────
def draw_face(img, cx, cy, s, skin):
    """A drawn face about 2s tall: YuNet finds these with scores near 0.9."""
    cv2.ellipse(img, (cx, cy - int(0.25 * s)), (int(0.95 * s), int(1.15 * s)), 0, 0, 360, (30, 30, 40), -1)
    cv2.ellipse(img, (cx, cy), (int(0.8 * s), int(s)), 0, 0, 360, skin, -1)
    for side in (-1, 1):
        ex, ey = cx + int(side * 0.33 * s), cy - int(0.15 * s)
        cv2.ellipse(img, (ex, ey), (int(0.17 * s), int(0.08 * s)), 0, 0, 360, (250, 250, 250), -1)
        cv2.circle(img, (ex, ey), int(0.07 * s), (40, 30, 20), -1)
        cv2.ellipse(img, (ex, ey - int(0.17 * s)), (int(0.2 * s), int(0.05 * s)), 0, 180, 360, (40, 40, 50),
                    int(max(2, 0.05 * s)))
    cv2.line(img, (cx, cy - int(0.05 * s)), (cx - int(0.06 * s), cy + int(0.25 * s)), (100, 120, 170),
             max(2, int(0.04 * s)))
    cv2.ellipse(img, (cx, cy + int(0.45 * s)), (int(0.25 * s), int(0.08 * s)), 0, 0, 360, (80, 60, 170), -1)


def frame(w, h, faces, texture):
    """A background gradient (plus blurred noise when `texture`), the faces, and a slight blur."""
    y, x = np.mgrid[0:h, 0:w]
    img = np.stack([70 + 60 * x / w, 90 + 50 * y / h, 110 - 40 * x / w], axis=-1)
    if texture:
        img += cv2.GaussianBlur(np.random.default_rng(5).normal(0, 10, (h, w, 3)), (0, 0), 1.2)
    img = np.clip(img, 0, 255).astype(np.uint8)
    for cx, cy, s, skin in faces:
        draw_face(img, cx, cy, s, skin)
    return cv2.GaussianBlur(img, (3, 3), 0)


def crop_now(frame_bgr, f):
    """The spike's Light-ASD crop (extract_asd.py), verbatim."""
    bs = max(f[2], f[3]) / 2
    cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
    bsi = int(bs * (1 + 2 * CS))
    img = cv2.copyMakeBorder(frame_bgr, bsi, bsi, bsi, bsi, cv2.BORDER_CONSTANT, value=(110, 110, 110))
    my, mx = cy + bsi, cx + bsi
    face = img[int(my - bs):int(my + bs * (1 + 2 * CS)), int(mx - bs * (1 + CS)):int(mx + bs * (1 + CS))]
    g = cv2.cvtColor(cv2.resize(face, (224, 224)), cv2.COLOR_BGR2GRAY)
    return g[56:168, 56:168].copy()


def alignment_matrix(recognizer, size, face):
    """alignCrop's similarity matrix, which OpenCV doesn't expose: align a float picture whose pixels are their own
    (x, y, 1) and fit the affine map from the 112x112 output back to the source (bilinear is exact on a linear ramp;
    warpAffine rounds positions to 1/32 px, which the least-squares fit over ~10k pixels averages out)."""
    w, h = size
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    coords = np.dstack([x, y, np.ones_like(x)])
    out = recognizer.alignCrop(coords, face).reshape(-1, 3).astype(np.float64)
    v, u = np.mgrid[0:112, 0:112]
    inside = out[:, 2] == 1.0
    design = np.column_stack([u.ravel(), v.ravel(), np.ones(112 * 112)])[inside]
    inverse, *_ = np.linalg.lstsq(design, out[inside, :2], rcond=None)
    return cv2.invertAffineTransform(inverse.T)


detector = cv2.FaceDetectorYN.create(str(source), "", (320, 320), SCORE)
recognizer = cv2.FaceRecognizerSF.create(str(MODELS / "face_recognition_sface_2021dec.onnx"), "")
pictures = {
    # a portrait episode frame (720 wide: padded to 736); one face runs off the right edge, and the Light-ASD crops
    # of the big one and the bottom one reach past the frame (its grey border)
    "a": frame(720, 1280, [(170, 330, 150, (140, 170, 215)), (520, 900, 70, (120, 150, 200)),
                           (672, 560, 80, (130, 160, 210)), (300, 1215, 60, (125, 155, 205))], texture=False),
    # small and textured, padded on both sides (360x240 -> 384x256)
    "b": frame(360, 240, [(115, 125, 48, (125, 155, 205)), (275, 105, 34, (140, 170, 215))], texture=True),
}
fixtures = {}
for key, img in pictures.items():
    h, w = img.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(img)
    faces = np.zeros((0, 15), np.float32) if faces is None else faces
    print(f"picture {key} ({w}x{h}): {len(faces)} faces, scores {np.round(faces[:, 14], 3).tolist()}")
    aligned = [recognizer.alignCrop(img, f) for f in faces]
    fixtures.update({
        f"{key}_png": np.frombuffer(cv2.imencode(".png", img)[1].tobytes(), np.uint8),
        f"{key}_faces": faces,
        f"{key}_matrices": np.array([alignment_matrix(recognizer, (w, h), f) for f in faces]),
        f"{key}_aligned": np.array(aligned),
        f"{key}_features": np.array([recognizer.feature(a).ravel() for a in aligned]),
        f"{key}_asd": np.array([crop_now(img, f) for f in faces]),
    })
np.savez_compressed(FIXTURES / "faces_parity.npz", **fixtures)
print("wrote", target, "and", FIXTURES / "faces_parity.npz")
