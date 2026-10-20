# Third-party notices

Packet Foundry's initial implementation was written for this repository. Weeyuga and
Optimus were architectural references; no source files from those projects are
redistributed in this implementation. If source is copied later, record its provenance,
license, and required notices here.

The Qwen Image 2.1 workflow example adapts the MIT-licensed Comfy-Org workflow
template. Its attribution and full MIT notice are preserved in
[examples/comfyui-qwen-image21.NOTICE.md](examples/comfyui-qwen-image21.NOTICE.md).
The model manifest links the separate Qwen Research terms; no model weights or
ComfyUI node implementation are included in the example.

Runtime dependencies and optional integrations retain their own licenses. Their
packages include their respective license metadata; distributing an assembled image
or environment also requires preserving applicable dependency notices. The lockfile
records resolved Python dependencies. Provider SDKs/services, PostgreSQL, Python,
container base images, Codex CLI, Claude CLI, Ollama, ComfyUI, Tailscale/Headscale, uv,
LangSmith, and Runpod are independently supplied projects or services, not relicensed
by Packet Foundry's Apache-2.0 license.
