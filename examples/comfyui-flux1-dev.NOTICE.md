# FLUX.1-dev example attribution

The API graph follows the node topology of the
[Comfy-Org FLUX dev text-to-image workflow](https://github.com/Comfy-Org/workflow_templates/blob/46e060ea88eb07adecf07197ec384c54a70e030c/templates/flux_dev_full_text_to_image.json).
The Packet Foundry example selects advanced GGUF diffusion loading and a GGUF
T5 encoder, fixes the canvas/sampler settings, removes UI/subgraph packaging,
and maps packet prompt/seed inputs. The graph uses the official workflow's
published CLIP-L and AE file sources, with exact revisions/hashes in the model
manifest. No ComfyUI node implementation or model weights are distributed here.

The upstream workflow repository is MIT-licensed; its notice follows. Model
weights retain the separate repository-specific terms linked in the
[model manifest](comfyui-flux1-dev.models.json). In particular the FLUX.1-dev
quantized diffusion checkpoint retains the upstream non-commercial model license;
quantization does not replace that license with Packet Foundry's code license.

MIT License

Copyright (c) 2023-present Comfy Org

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
