# Qwen Image 2.1 example attribution

The Qwen Image 2.1 API graph follows the node connections in the
[Comfy-Org text-to-image workflow](https://github.com/Comfy-Org/workflow_templates/blob/46e060ea88eb07adecf07197ec384c54a70e030c/templates/image_qwen_image_2_1_t2i.json).
The Packet Foundry example replaces its diffusion loader with the advanced GGUF
loader, selects CPU text encoding and 512-pixel output, disables the Qwen prefix
KV cache, removes unrelated UI/workflow elements, and maps packet prompt/seed
inputs. No ComfyUI node implementation or model weights are distributed here.

The upstream workflow repository is MIT-licensed; its notice follows. Model
weights instead retain the separate Qwen Research terms linked in the
[model manifest](comfyui-qwen-image21.models.json).

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
