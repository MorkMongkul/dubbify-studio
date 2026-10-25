# Licences of the bundled models

## Light-ASD (`lightasd-ava.onnx`)

Lip-sync model: is this face speaking this sound? Exported to ONNX from the AVA-trained weights
(`weight/pretrain_AVA_CVPR.model`) by `scripts/export-light-asd.py`.

- Source: https://github.com/Junhua-Liao/Light-ASD
- Commit: `ed38c232de5efe0261dbd68627c0ade7cdfe14eb`
- Licence: MIT

```
MIT License

Copyright (c) 2023 Liao Junhua

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Training data: the weights are `pretrain_AVA_CVPR.model`, trained by the Light-ASD authors on the AVA-ActiveSpeaker
dataset (https://research.google.com/ava/). Google publishes AVA's annotations under CC BY 4.0; the dataset's videos are
third-party YouTube content and are not redistributed here. Whether the AVA terms place any condition on using
weights trained on it in a commercial product: **to confirm** (not verified from the dataset's own terms page; Light-ASD
itself carries only the MIT licence above).

## ERes2NetV2 (`3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx`)

Voice print: a vector that says whose voice a stretch of speech is. Fetched by `scripts/fetch-models.sh` (not stored in
git). It is the 3D-Speaker project's ERes2NetV2 speaker model for Mandarin, converted to ONNX and published in the
sherpa-onnx "speaker-recongition-models" release; the backend runs it through the `sherpa-onnx` library (Apache-2.0).

- Source: https://github.com/modelscope/3D-Speaker (code and model family); model card
  https://modelscope.cn/models/iic/speech_eres2netv2_sv_zh-cn_16k-common
- Licence: Apache License 2.0. Verified from the repository (`LICENSE` and its README's "License" section: "3D-Speaker is
  released under the Apache License 2.0") and from the model card's licence field ("Apache License 2.0").
- Distributed through: https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models (that release
  says each model keeps its own licence; the one above is this model's).

## SFace (`face_recognition_sface_2021dec.onnx`)

Face print: a vector that says whose face a picture is. Fetched by `scripts/fetch-models.sh` (not stored in git).

- Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
- Licence: Apache License 2.0. Verified from that folder's `README.md` ("All files in this directory are licensed under
  Apache 2.0 License") and its `LICENSE` file.

## YuNet (`face_detection_yunet_2023mar.onnx`)

Face detection: where the faces in a picture are. Stored in git.

- Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- Licence: MIT, Copyright (c) 2020 Shiqi Yu. Verified from that folder's `README.md` ("All files in this directory are
  licensed under MIT License") and its `LICENSE` file, which is the standard MIT text shown above for Light-ASD.

## Libraries that run the models

The app contains no OpenCV: its macOS wheel bundles a whole FFmpeg with GPL libraries (x264, x265, and others), and the
app ships only an LGPL ffmpeg. `backend/build_sidecar.sh` stops if such a library gets into the build.

- `onnxruntime` 1.23.2 (runs YuNet, SFace and Light-ASD): MIT, Copyright (c) Microsoft Corporation.
  https://github.com/microsoft/onnxruntime/blob/main/LICENSE
- `sherpa-onnx` 2.0.0 and `sherpa-onnx-core` 2.0.0 (runs ERes2NetV2): Apache License 2.0.
  https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE (PyPI lists sherpa-onnx-core as Apache-2.0 too).
  sherpa-onnx carries its own copy of `libonnxruntime.dylib` (in `sherpa_onnx/lib`), separate from the `onnxruntime`
  package's; both are in the build.
