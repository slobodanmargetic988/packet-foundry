# YuE2 example attribution

The API graph follows the music-generation, diffusion, decode and FLAC-save
connections in the [Comfy-Org YuE2 text-to-music API benchmark](https://github.com/Comfy-Org/workflow_templates/blob/46e060ea88eb07adecf07197ec384c54a70e030c/benchmarks/audio_yue2_text2music.json).
The Packet Foundry example flattens the graph, removes optional ABC score-planning
and UI/preview nodes, caps generation at 15 seconds, supplies original synthetic
test lyrics, fixes the operator-approved generation settings and maps the packet
style prompt and seed. The eight-node GPU preparation adds the stock ComfyUI
`SelectCLIPDevice` interface for autoregressive CLIP placement on `gpu:0`; it does
not copy or patch that node implementation. With an empty ABC input, the pinned
music node selects its off mode despite the retained full widget value, so no
ABC score-generation stage is executed. No upstream demonstration song, model weights or ComfyUI
node implementation are distributed here. A 15-second maximum is a cap, not a
promise of exact output duration or generation speed.

The upstream workflow repository is MIT-licensed; its notice follows. The model
weights instead retain CC-BY-NC-4.0 as declared by the pinned repackaged and
original model cards linked in the [model manifest](comfyui-yue2.models.json).
Packet Foundry's code license does not change those model terms.

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
