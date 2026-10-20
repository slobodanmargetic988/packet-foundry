# Third-party notices

Packet Foundry's initial implementation was written for this repository. Weeyuga and
Optimus were architectural references; no source files from those projects are
redistributed in this implementation. If source is copied later, record its provenance,
license, and required notices here.

The Qwen Image 2.1 workflow example adapts the MIT-licensed Comfy-Org workflow
template. Its attribution and full MIT notice are preserved in
[example workflow notice](https://github.com/slobodanmargetic988/packet-foundry/blob/main/examples/comfyui-qwen-image21.NOTICE.md).
The model manifest links the separate Qwen Research terms; no model weights or
ComfyUI node implementation are included in the example.

The FLUX.1-dev workflow example also adapts an MIT-licensed Comfy-Org workflow.
Its attribution and full MIT notice are preserved in
[FLUX.1-dev workflow notice](https://github.com/slobodanmargetic988/packet-foundry/blob/main/examples/comfyui-flux1-dev.NOTICE.md).
Its model manifest records the separate FLUX.1-dev, T5, CLIP-L and AE repository
license declarations; no model weights or node implementation are included.

The YuE2 audio workflow example adapts an MIT-licensed Comfy-Org workflow.
Its attribution and full MIT notice are preserved in
[YuE2 workflow notice](https://github.com/slobodanmargetic988/packet-foundry/blob/main/examples/comfyui-yue2.NOTICE.md).
Its manifest links the separate CC-BY-NC-4.0 model-card declarations. No model
weights or ComfyUI node implementation are included.

Runtime dependencies and optional integrations retain their own licenses. Their
packages include their respective license metadata; distributing an assembled image
or environment also requires preserving applicable dependency notices. The lockfile
records resolved Python dependencies. Provider SDKs/services, PostgreSQL, Python,
container base images, Codex CLI, Claude CLI, Ollama, ComfyUI, Tailscale/Headscale, uv,
LangSmith, and Runpod are independently supplied projects or services, not relicensed
by Packet Foundry's Apache-2.0 license.
