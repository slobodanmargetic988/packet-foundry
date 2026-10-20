# Native Windows ComfyUI image factory

This guide reproduces verified FLUX.3 Image Klein step-distilled Q4_K_M and Qwen
Image 2.1 Q4_K_M configurations on a Windows 10 GTX 1060 with 6 GB VRAM. Real
packets completed through Factory, Registry and Warehouse to a private generic
HTTP verification receiver, and the downloaded PNGs matched their LangSmith
attachments. See [FLUX evidence](evidence/2026-10-20/windows-comfyui-image.json)
and [Qwen evidence](evidence/2026-10-20/windows-qwen-image21.json).
These are functional examples, not guarantees for every 6 GB GPU or another model.
A sixteen-step FLUX packet also completed after a factory-only process restart
while the separate ComfyUI process remained running. The Qwen check includes two
consecutive generations with a changed prompt and seed.

Use separate native environments for the factory and ComfyUI. The factory needs
only its outbound registry connection and the local ComfyUI server; the machine
does not host Warehouse, Registry or Collector. No WSL or public model-server port
is required. Paths below are generic examples; choose new directories that do not
replace an existing model runtime.

## Runtime and model pins

The verified runtime used the official
[ComfyUI v0.42.1 CUDA 12.6 portable release](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.42.1):

| Component | Verified value |
| --- | --- |
| ComfyUI source | `63be2f9b82ad0dac88a7ceaa28a9cf3154125a18` |
| Portable asset | `ComfyUI_windows_portable_nvidia_cu126.7z` |
| Portable size | 1,910,153,206 bytes |
| Portable SHA-256 | `0506abc6563ea5928a052fe0bc0b32c2a063098558bd593fe39345a27f1e47fc` |
| Embedded Python / Torch | 3.12.10 / 2.14.0+cu126 |
| comfy-kitchen | 0.2.39 |
| ComfyUI-GGUF source | `6ea2651e7df66d7585f6ffee804b20e92fb38b8a` |
| GGUF / sentencepiece / protobuf | 0.19.0 / 0.2.2 / 7.36.2 |
| Factory / contracts | Public v0.1.2 wheels in a separate native Python environment |

The tested CUDA 12.6 build includes SM 6.1. Do not replace its Torch installation
with an arbitrary newer CUDA wheel and assume old-GPU support remains available.
Native BF16 was unavailable on the tested GPU. The conservative command below
uses ordinary FP32 model computation, CPU text encoding and CPU VAE decoding.

Download the official portable archive and check it before extraction:

```powershell
Get-FileHash .\ComfyUI_windows_portable_nvidia_cu126.7z -Algorithm SHA256
```

Extract it with a tool that supports 7z. Pin the reviewed
[ComfyUI-GGUF node](https://github.com/city96/ComfyUI-GGUF/tree/6ea2651e7df66d7585f6ffee804b20e92fb38b8a)
and install its dependencies only into the embedded environment:

```powershell
$ComfyRoot = 'C:\FoundryRuntime\ComfyUI_windows_portable'
git clone https://github.com/city96/ComfyUI-GGUF "$ComfyRoot\ComfyUI\custom_nodes\ComfyUI-GGUF"
git -C "$ComfyRoot\ComfyUI\custom_nodes\ComfyUI-GGUF" checkout 6ea2651e7df66d7585f6ffee804b20e92fb38b8a
& "$ComfyRoot\python_embeded\python.exe" -m pip install gguf==0.19.0 sentencepiece==0.2.2 protobuf==7.36.2
```

The [model manifest](../examples/comfyui-flux3-klein.models.json) contains exact
revision URLs, lengths, hashes and destinations for the three required files:

- `flux3_image_klein_sd_Q4_K_M.gguf` in `ComfyUI/models/diffusion_models`.
- `qwen3vl_4b_int8_convrot.safetensors` in `ComfyUI/models/text_encoders`.
- `flux2-vae.safetensors` in `ComfyUI/models/vae`.

Obtain access to the official gated repository through its normal account flow
and review the upstream [FLUX Kommunity License v1.0](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy/blob/feef60b0aaf63a4773ace96636ebbcf43b881312/LICENSE.md)
and notices. Model weights have their own terms, separate from Packet Foundry's
code license. Keep download credentials out of shell
arguments, JSON, logs and commits. Verify both length and SHA-256 before using each
file. No optional prompt upsampler or alternate text encoder is needed by this
example. The graph expects a complete scene caption; expand a short request
upstream before packaging it, as described in the [model's prompting guide](https://huggingface.co/black-forest-labs/flux-3-image-klein#prompting). The tested
request already described subject, setting, lighting and framing. A successful
download or node import is not proof of generation.

## Start the local model server

The actual image check used these ComfyUI settings. Run this in a foreground console
first; adjust the 2.2 GiB reserve only after observing the machine's other workloads.
It is a model-memory setting, not a hard physical GPU cap.

```powershell
& "$ComfyRoot\python_embeded\python.exe" -s "$ComfyRoot\ComfyUI\main.py" `
  --listen 127.0.0.1 --port 8188 --disable-auto-launch `
  --disable-dynamic-vram --lowvram --disable-cuda-malloc `
  --disable-async-offload --disable-comfy-compiler --disable-triton-backend `
  --disable-xformers --use-split-cross-attention `
  --fp32-unet --fp32-text-enc --cpu-vae --fp32-vae --reserve-vram 2.2 `
  --offline --disable-all-custom-nodes --whitelist-custom-nodes ComfyUI-GGUF
```

Keep the listener local. Check the server's `/system_stats` and `/object_info`,
then verify that all nine nodes (eight distinct classes) and the exact three model filenames from the
[backend example](../examples/comfyui-flux3-klein.backend.json) are available.
The API graph uses the advanced GGUF loader with both `dequant_dtype=float32` and
`patch_dtype=float32`, `patch_on_device=false`, core `CLIPLoader` with `type=flux3`
and `device=cpu`, a Flux 2 latent and VAE, Euler/simple at CFG 1, four steps, and
`SaveImage`. It keeps the diffusion weights quantized and dequantizes layers for
ordinary computation. Its decoder remains on CPU.

Other GPUs and memory configurations may need different settings. Do not enable
BF16, FP8, tensor-core attention, parallel model residency or a larger canvas merely
because the server starts successfully. The verified image used one shared GPU
slot. No exact GPU peak measurement was collected.

## Connect the factory

Install the public factory/contracts wheels in a separate native virtual
environment following [factory installation](factory.md). Enroll a unique factory
identity in the private registry. Store its issued credential in a private file
readable by the current user and the intended service account only; on Windows,
use NTFS ACLs rather than relying on Unix permission bits. Set
`registry_token_env=null` and `registry_token_file` to that file in the private
configuration. Never copy another machine's factory identity or token.

Put the complete backend example under `backends.comfyui-local`. Its template ID
is `flux3-klein-q4km-512-v1`; the example uses a generic ID rather than the private
deployment's identifier. Advertise a matching profile, for example:

```json
{
  "profile_id": "flux3-klein-q4km-512",
  "model": "Abiray/flux-3-image-klein-sd-GGUF:Q4_K_M",
  "backend": "comfyui-local",
  "resource_group": "local-gpu",
  "input_kinds": ["text", "json"],
  "output_kinds": ["image"],
  "supports_live": false,
  "accept_experiments": false,
  "templates": ["flux3-klein-q4km-512-v1"]
}
```

Set `registration.resource_groups.local-gpu` to 1. Give any other profile using
the same GPU that same resource group. Keep the profile image-only until another
approved workflow is actually verified. In this template, packets can supply the
resolved prompt and seed; they cannot change canvas size, sampler, local graph,
credentials or arbitrary node fields.

Run `foundry-factory doctor --network --config factory.json`, then start the
factory in the foreground. Submit a single normal-priority test packet with this
exact model/template and PNG output. Confirm the terminal result, expected factory
profile, a fully decoded 512-square image, artifact checksum, durable application
save and Warehouse ACK. The published image proof used a generic HTTP receiver;
it did not exercise a Collector SDK image import or a Media Presence image UI.
Receiver replay after ACK also re-downloaded the same retained artifact and repeated
ACK without a new generation request; an ACKed result is absent from the outbox, so
the receiver must retain its own saved result envelope.
Visual inspection should check the requested content; a valid PNG alone is not
enough. Optional LangSmith content capture should be enabled only when the inputs
and generated output may be sent there. See [tracing configuration](factory.md#optional-langsmith).

The verified cold run took 127.90 seconds in ComfyUI, including loading, prompt
encoding and decoding. Its factory trace lasted about 130 seconds. These values
describe one request, not warm throughput, a model comparison or a time guarantee.

## Qwen Image 2.1 example

Qwen Image 2.1 Q4_K_M produced two consecutive, visually checked 512×512 PNGs
through the same Windows factory and receiver. Both completed on their first
attempt, were downloaded/ACKed, and matched their independently decoded LangSmith
attachments. Native experiment API checks verified one successful run per batch
with the expected model, seed, profile, accepted trace and artifact links. The
[Qwen evidence](evidence/2026-10-20/windows-qwen-image21.json) keeps an earlier
successful generation separate from this clean pair.

Use the same pinned ComfyUI/GGUF runtime and conservative server command above.
The [Qwen model manifest](../examples/comfyui-qwen-image21.models.json) pins three
different weights: the `chfm/Qwen-Image-2.1-GGUF` Q4_K_M diffusion model,
`qwen3vl_8b_int8_convrot.safetensors` CPU text encoder, and
`qwen_image_2.1_vae_bf16.safetensors`. The VAE's filename describes its stored
weights; the tested launch runs its computation in FP32 on CPU.

These weights use the
[Qwen RESEARCH LICENSE AGREEMENT](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/f52e6a529129fff6a994469561673ea45a84043d/LICENSE),
which permits non-commercial research/evaluation and requires a separate license
for commercial use. These checks are evaluation trials. Packet Foundry's Apache-2.0
license does not change the weights' terms. The graph follows the upstream
[Comfy-Org Qwen text-to-image workflow](https://github.com/Comfy-Org/workflow_templates/blob/46e060ea88eb07adecf07197ec384c54a70e030c/templates/image_qwen_image_2_1_t2i.json),
with the adaptations and MIT notice recorded in the
[example attribution](../examples/comfyui-qwen-image21.NOTICE.md).

Put the [Qwen backend example](../examples/comfyui-qwen-image21.backend.json)
under the configured backend alias, or merge its template into the existing
ComfyUI backend's `templates`. Do not replace another model's templates when
adding this one. Advertise the image-only model
`chfm/Qwen-Image-2.1-GGUF:Q4_K_M`, template `qwen-image21-q4-512-v1`, with the
same GPU resource group and capacity one as the FLUX profile. Check that all
eight graph classes and the exact three model filenames are available before
submitting a packet.

The graph uses FP32 advanced GGUF dequantization/patches, `CLIPLoader` with
`type=qwen_image` and `device=cpu`, and core `TextEncodeQwenImage21`. With no
reference images and resolution 512, that node provides positive conditioning,
negative conditioning and the `[1, 64, 32, 32]` latent directly to the sampler.
Do not substitute the FLUX latent node. Sampling uses 25 Euler/simple steps,
CFG 1, followed by CPU VAE decode and `SaveImage`. The model's prefix KV cache
is deliberately `off`; this is separate from ComfyUI's node/model output cache.
Only prompt and seed are mapped from packets. The six-hour backend deadline is
the actual functional configuration, not a prediction of generation time.

The clean first request ran after a supported model unload and node-cache clear;
no cached nodes were reported. The operating system's file cache was not cleared,
so this is cold model/node state, not cold disk. The second request changes the
fox brief from red to blue and seed 42 to 43 without a model unload or process
restart. This requires fresh text encoding and sampling while allowing loader/model
objects to be reused. It does not guarantee that all weights remain on the GPU.

The server's execution-start/success history gives these intervals:

| Observation | Red brief, seed 42, model/node state cleared | Blue brief, seed 43, model/node state preserved |
| --- | --- | --- |
| Total server execution | 711.760 s | 390.925 s |
| Text-encoding node | 367.881 s | 144.801 s |
| KSampler node, including model preparation | 273.813 s | 215.816 s |
| CPU VAE decode node | 36.509 s | 29.350 s |
| Final sampler progress-bar elapsed, rounded | about 219 s | about 215 s |

Only total execution uses server timestamp boundaries. Node intervals use observer
receipt times, include preparation inside the node, and are not exact weight-loading
timings. The second run reused loader/configuration nodes 1, 2, 4 and 5; text node
3 and sampler node 6 executed again, with all 25 sampler progress events observed.
Its output was a blue fox rather than the first run's red fox. The changed prompts
have intentionally different experiment case identities on the same dataset;
this is not a same-case quality comparison. LangSmith's media and comparison UI
were not inspected.

An earlier separate successful request took 474.45 seconds. Background activity,
OS caching and substantial observed variation make these functional observations,
not a throughput benchmark or time guarantee. Resource samples are not continuous
memory peaks. A previous seed-only repeat completed locally but lost its lease
during a network failure; it is excluded from this successful delivery pair.

## Login startup and verification limits

Install current-user startup only after foreground generation works. The factory's
`install-startup` command starts the factory, not ComfyUI. Arrange a separate
current-user ComfyUI login task and make the factory wrapper wait for local model
readiness before running. The checked deployment used limited interactive tasks,
a 15-second factory login delay and a bounded five-minute readiness wait. It did
not enable automatic Windows login. Use absolute configuration/credential paths
because task environments differ from an interactive terminal.

Installed and running tasks have been observed. An actual factory-only process
restart during a sixteen-step generation resumed the same attempt, fence and
provider prompt with one enqueue and a correct accepted image. ComfyUI stayed
running throughout. The final trace starts after the restart; the interrupted
original trace remains unclosed and outputless. Both accepted image results have
matching trace attachments and native experiment runs on a shared dataset/case.
Their comparison UI was not inspected.

Fresh logout/login, physical reboot, sleep/resume and network interruption remain
separate checks; do not label them verified from task configuration alone.
Preserve existing models, drivers and unrelated services while testing. A server
restart that loses prompt history is a different recovery case from restarting
only the factory, and remains unverified here.

Public evidence deliberately omits deployment addresses, user directories,
machine names, factory credentials and tracing workspace identifiers. Keep the
original detailed operator records private.
