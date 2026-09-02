# Third-party notices for image redaction

This file records the upstream implementations intentionally reused by the
watermark-removal and logo-mosaic feature. Model files are not committed to Git;
their manifests must carry the same source and license metadata.

## remove-ai-watermarks

- Repository: https://github.com/wiltodelta/remove-ai-watermarks
- Pinned release: `0.37.0`
- Reviewed commit: `9bb4e9cd54879f7072f14be6560a4b7a54d7d7a8`
- License: Apache License 2.0
- Use: region eraser, OpenCV/MI-GAN dispatch and mask polarity.
- Integration: mask polarity and dispatch behavior were adapted behind
  `src/services/images/adapters/remove_ai_watermarks.py`. The upstream package
  remains available through the `redaction-reference` extra for reproducible
  comparison, but is not shipped in the lean production runtime.

## GeminiWatermarkTool

- Repository: https://github.com/allenk/GeminiWatermarkTool
- Pinned release: `v0.3.2`
- Reviewed commit: `7c6a99f2c3df97eb3c430ef87a0c962aea5cb80e`
- Copyright: Copyright (c) 2024 AllenK (Kwyshell)
- License: MIT License. The full license text follows.
- Use: reverse alpha-blending equation, alpha thresholding and template-alignment
  design. Gemini mask assets are not copied; Dangjia-specific assets must be
  calibrated from authorized pairs.

```text
MIT License

Copyright (c) 2024 AllenK (Kwyshell)

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

## YOLOX

- Repository: https://github.com/Megvii-BaseDetection/YOLOX
- Reviewed commit: `6ddff4824372906469a7fae2dc3206c7aa4bbaee`
- License: Apache License 2.0
- Use: training/export workflow and ONNX Runtime preprocessing/postprocessing.
- The local experiment file `training/yolox/dangjia_logo_nano.py` is adapted
  from the official `exps/default/yolox_nano.py` and retains its copyright and
  SPDX notice. Training and export invoke the pinned upstream tools directly.
- The production image contains ONNX Runtime inference only, not the PyTorch
  training stack.

## OpenCV

- Repository: https://github.com/opencv/opencv
- License: Apache License 2.0
- Use: image decoding/encoding, morphology, Telea inpainting and pixelation.

## OpenCV LaMa inpainting model

- Model repository: https://huggingface.co/opencv/inpainting_lama
- Upstream algorithm: https://github.com/advimman/lama
- Model artifact: `inpainting_lama_2025jan.onnx`
- Model SHA-256: `7df918ac3921d3daf0aae1d219776cf0dc4e4935f035af81841b40adcf74fdf2`
- License: Apache License 2.0
- Use: official 512 x 512 OpenCV DNN preprocessing and content-aware repair.

## RapidOCR

- Repository: https://github.com/RapidAI/RapidOCR
- Reviewed commit: `0e629c8be05635035c01a829d10a91bbcd56a27a`
- Pinned package: `rapidocr==3.9.2`
- License: Apache License 2.0
- Use: PaddleOCR-derived text detection polygons inside the protected bottom-left
  ROI only. Recognized address/date text is not persisted.
- Runtime: OpenVINO CPU backend, isolated in the redaction dependency group.
