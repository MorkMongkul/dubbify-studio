# Licences of the bundled models

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

## CAM++ (`3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`)

Voice print, the second half of a line's print since 0.5.3 (characters.voiceprint). Fetched by
`scripts/fetch-models.sh` (not stored in git). It is the 3D-Speaker project's CAM++ speaker model for Mandarin and
English ("advanced"), converted to ONNX and published in the same sherpa-onnx release as ERes2NetV2.

- Source: https://github.com/modelscope/3D-Speaker; model card
  https://modelscope.cn/models/iic/speech_campplus_sv_zh_en_16k-common_advanced
- Licence: Apache License 2.0 (3D-Speaker's, as for ERes2NetV2 above).
- SHA-256 pinned in `models.json` from the file downloaded on 2026-10-30 (the release lists no digest for it).

The face models of 0.5.0–0.5.2 (YuNet, SFace, Light-ASD) left the app in 0.5.3.

## Libraries that run the models

The app contains no OpenCV: its macOS wheel bundles a whole FFmpeg with GPL libraries (x264, x265, and others), and the
app ships only an LGPL ffmpeg. `backend/build_sidecar.sh` stops if such a library gets into the build.

- `sherpa-onnx` 2.0.0 and `sherpa-onnx-core` 2.0.0 (runs ERes2NetV2 and CAM++): Apache License 2.0.
  https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE (PyPI lists sherpa-onnx-core as Apache-2.0 too).
- ONNX Runtime, as the copy of `libonnxruntime.dylib` that sherpa-onnx carries (in `sherpa_onnx/lib`): MIT, Copyright (c)
  Microsoft Corporation. https://github.com/microsoft/onnxruntime/blob/main/LICENSE (the `onnxruntime` package itself
  ran the face models and left the app with them in 0.5.3).
