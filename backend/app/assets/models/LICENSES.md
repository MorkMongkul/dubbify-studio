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
