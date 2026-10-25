#!/usr/bin/env python3
"""Dev-only: export Light-ASD (lip-sync / "is this face speaking this sound?") to ONNX.

Run once with a Python that has torch, onnx, onnxruntime, python_speech_features and scipy; the app itself
never needs torch. Commit only the outputs:
  backend/app/assets/models/lightasd-ava.onnx
  backend/app/tests/fixtures/lightasd_parity.npz   (seeded input + torch's scores)
  backend/app/tests/fixtures/mfcc_reference.npz    (a seeded 1 s int16 signal + python_speech_features' MFCCs)

Light-ASD: https://github.com/Junhua-Liao/Light-ASD (MIT), pinned to commit
ed38c232de5efe0261dbd68627c0ade7cdfe14eb, weights weight/pretrain_AVA_CVPR.model (trained on AVA).

  git clone https://github.com/Junhua-Liao/Light-ASD && git -C Light-ASD checkout ed38c232de5efe0261dbd68627c0ade7cdfe14eb
  python scripts/export-light-asd.py Light-ASD

The graph is exactly the demo's forward: audio [1, 4T, 13] float32 (MFCC, 100 per second) and video
[1, T, 112, 112] float32 (grey face crops, 25 per second, 0-255) -> scores [T] (> 0: speaking).
"""
import copy
import sys
from pathlib import Path

import numpy as np
import onnxruntime
import python_speech_features
import torch
import torch.nn as nn

COMMIT = "ed38c232de5efe0261dbd68627c0ade7cdfe14eb"
ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "backend/app/assets/models"
FIXTURES = ROOT / "backend/app/tests/fixtures"
TOLERANCE = 1e-4

repo = Path(sys.argv[1] if len(sys.argv) > 1 else "Light-ASD").resolve()
sys.path.insert(0, str(repo))
from loss import lossAV  # noqa: E402
from model.Model import ASD_Model  # noqa: E402


class LightASD(nn.Module):
    """ASD_Model + lossAV.FC, the way the demo scores a clip (lossAV.forward with labels None)."""

    def __init__(self):
        super().__init__()
        self.model = ASD_Model()
        self.lossAV = lossAV()

    def forward(self, audio, video):
        m = self.model
        out = m.forward_audio_visual_backend(m.forward_audio_frontend(audio), m.forward_visual_frontend(video))
        return self.lossAV.FC(out)[:, 1]  # lossAV's squeeze(1) is a no-op on [T, 128]


net = LightASD()
state = torch.load(repo / "weight/pretrain_AVA_CVPR.model", map_location="cpu", weights_only=True)
net.load_state_dict({k.replace("module.", ""): v for k, v in state.items() if not k.startswith("lossV")}, strict=False)
net.eval()
reference_net = copy.deepcopy(net)  # untouched: the torch scores in the checks and fixture come from this

# The audio encoder feeds 4-D tensors to nn.MaxPool3d (kernel (1, 1, 3) over time), which torch's exporter writes
# as a 3-D pool that onnxruntime rejects. The same pooling over the last axis of a 4-D tensor is a MaxPool2d.
for name in ("pool1", "pool2"):
    assert isinstance(getattr(net.model.audioEncoder, name), nn.MaxPool3d)
    setattr(net.model.audioEncoder, name, nn.MaxPool2d(kernel_size=(1, 3), stride=(1, 2), padding=(0, 1)))

rng = np.random.default_rng(7)
T = 10
audio = rng.standard_normal((1, 4 * T, 13)).astype(np.float32) * 20
video = rng.integers(0, 256, (1, T, 112, 112)).astype(np.float32)
with torch.no_grad():
    expected = reference_net(torch.from_numpy(audio), torch.from_numpy(video)).numpy()

MODELS.mkdir(parents=True, exist_ok=True)
FIXTURES.mkdir(parents=True, exist_ok=True)
target = MODELS / "lightasd-ava.onnx"
torch.onnx.export(
    net, (torch.from_numpy(audio), torch.from_numpy(video)), str(target), input_names=["audio", "video"],
    output_names=["scores"], dynamic_axes={"audio": {1: "audio_frames"}, "video": {1: "video_frames"}, "scores": {0: "video_frames"}},
    opset_version=17, dynamo=False)

session = onnxruntime.InferenceSession(str(target), providers=["CPUExecutionProvider"])
diffs = []
for t in (T, 25, 100):  # the length must really be dynamic
    a = rng.standard_normal((1, 4 * t, 13)).astype(np.float32) * 20
    v = rng.integers(0, 256, (1, t, 112, 112)).astype(np.float32)
    with torch.no_grad():
        ref = reference_net(torch.from_numpy(a), torch.from_numpy(v)).numpy()
    out = session.run(None, {"audio": a, "video": v})[0]
    assert out.shape == ref.shape == (t,), (out.shape, ref.shape)
    diffs.append(float(np.abs(out - ref).max()))
worst = max(diffs)
print(f"ONNX vs torch, max abs diff over T=10/25/100: {worst:.2e}")
assert worst < TOLERANCE, worst
np.savez_compressed(FIXTURES / "lightasd_parity.npz", audio=audio, video=video, scores=expected)

signal = (rng.standard_normal(16000) * 3000).astype(np.int16)
reference = python_speech_features.mfcc(signal, 16000, numcep=13, winlen=0.025, winstep=0.010)
np.savez_compressed(FIXTURES / "mfcc_reference.npz", signal=signal, mfcc=reference)
print("wrote", target, "and the two fixtures")
