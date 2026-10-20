# Verification matrix

Status as of **20 October 2026**: working implementation, with two physical Windows
factories deployed from public release **v0.1.2** for real text/JSON, image and
YuE2 audio work. The four-service stack is exercised end to end, including real containers and PostgreSQL. Complete
live verification of every media/provider integration is still pending. Do not treat
synthetic HTTP responses or mocked cloud lifecycle tests as real model/GPU evidence.

## Confirmed checks

| Check | Evidence | Boundary |
| --- | --- | --- |
| Local automated suite (19 October) | **129 passed**, Python 3.12.13 on macOS ARM64, real PostgreSQL | Includes synthetic provider fixtures; no paid inference in tests |
| Mixed batch | Two actual FactoryDaemon instances processed **300 text/JSON packets**, consumed/ACKed once through Collector/Warehouse/Registry | HTTP applications in-process; inference responses synthetic |
| Docker quick start | Four separate service containers, PostgreSQL 17, both migrations, healthy servers, complete packet result and ACK | Linux ARM64 Docker; fixture provider synthetic |
| Docker recovery | Warehouse/Registry recreated and Factory/Collector restarted; second packet completed/ACKed; all pending journals empty | Same retained test volumes |
| Database isolation | Warehouse/Registry use separate roles/databases; cross-database connection refused | Compose deployment |
| Durable protocol | Duplicate submissions, shared specs, concurrent claims, capacity, lease fencing/expiry, cancellation/renewal races, bounded retries, ACK persistence | Real PostgreSQL integration tests |
| Experiment export | Two native experiments share two cases and the same dataset; four successful local generations from two factories link to their traces. The completed physical-Windows application batch also exported as a native experiment with two runs | Actual service API verified; comparison UI not visually inspected |
| Media trace transport | Actual SDK uploaded two synthetic PNG attachments; stored bytes were downloaded and matched | Proves transport, not model media generation or UI rendering |
| Packaging | All five wheels build; independent factory installation from wheel bundle succeeds | Native installation tested in an isolated local tool environment |
| Physical Windows factory | Public v0.1.2 checksums and installed versions verified; real llama.cpp text/JSON output, HTTPS registry registration/heartbeat and running current-user startup task | Existing local model runtime; user already logged in; physical recovery checks pending |
| Physical Windows image factory | Actual ComfyUI FLUX.3 Image Klein Q4_K_M, Qwen Image 2.1 Q4_K_M and FLUX.1-dev Q4_0 each passed a consecutive red/blue prompt and seed 42/43 pair. Separate FLUX.3 checks include the original image and a factory-only restart with no extra enqueue. All accepted results were visually checked, downloaded/ACKed and matched their LangSmith attachments | GTX 1060 6 GB functional checks; YuE2 audio is verified separately below. Additional models, video, OS and model-server recovery remain unverified |
| Physical Windows audio factory | YuE2 produced two consecutive real 15-second stereo FLACs, fully decoded/saved/ACKed with matching LangSmith attachments and native experiments. The user listened to both actual previews and confirmed usable music | Human usability feedback differs from automated signal checks; lyric accuracy, genre faithfulness, comparative quality and audio interruption recovery remain unassessed |
| Real application delivery | Browser requested two LinkedIn drafts; physical Windows factory generated both, Collector imported them, and production Feed showed both as **Awaiting review**. Exact submission replay returned the same job and no extra batch | Two actual text packets; neither draft approved or queued for publishing |

Container evidence: [deployment and restart summary](evidence/2026-10-19/compose.json).
Native Windows evidence: [installation, startup and local generation](evidence/2026-10-20/windows-native.json).
Native GPU image evidence: [real ComfyUI image delivery and trace attachment](evidence/2026-10-20/windows-comfyui-image.json).
Qwen image evidence: [consecutive fresh-prompt generation and model-cache observations](evidence/2026-10-20/windows-qwen-image21.json).
FLUX.1-dev evidence: [two real Q4_0 image deliveries and model-cache observations](evidence/2026-10-20/windows-flux1-dev.json).
YuE2 audio evidence: [two real GPU audio deliveries, user usability feedback and tracing](evidence/2026-10-20/windows-yue2.json).
Real provider evidence below contains only synthetic smoke-test content, no credentials.

## Real generation and external integration status

| Backend | Actual result | Remaining validation |
| --- | --- | --- |
| Codex subscription CLI | **Text and JSON passed**, model `gpt-6.1-sol`, CLI `0.163.0-alpha.10.2` | Other installed CLI versions/accounts and native Windows authentication |
| Ollama | **Text and JSON passed**, local `qwen2.5-coder:0.5b` | Other model/runtime/hardware combinations |
| OpenAI API | Request reached API; HTTP 429 with `credit_balance_exhausted` / `insufficient_quota` | Successful text, JSON, and speech with a funded configured account |
| Claude subscription CLI | Account reported organization-disabled subscription access; another probe timed out | Successful generation after account policy permits access |
| Anthropic API | Protocol fixtures pass; no API credential configured for live checks | Real text/JSON generation |
| Google image / Veo video | Protocol, binary handling, durable operation ID/poll/resume fixtures pass | Configured Google credential and real image/video outputs |
| ComfyUI | **Image passed:** actual FLUX.3 Image Klein Q4_K_M, Qwen Image 2.1 Q4_K_M and FLUX.1-dev Q4_0 on Windows/GTX 1060, approved graphs, result download/checksum/ACK and matching LangSmith attachments. All three passed consecutive fresh-prompt generations. A separate FLUX.3 factory-only restart resumed the same attempt/fence/prompt with one provider enqueue. **YuE2 audio passed:** two real 15-second FLACs, full decode/save/ACK, exact attachments/native experiments, positive user usability feedback | Other models, reference inputs, full video, lyric accuracy/comparative audio quality, audio recovery, model-server restart/history loss and OS/network recovery |
| OpenAI-compatible server | **Text and JSON passed** on a physical Windows laptop against existing llama.cpp Vulkan, `gemma-4-E2B-it-Q4_K_M.gguf`; JSON also passed against Ollama's compatibility endpoint, `qwen2.5-coder:0.5b` | Other compatible inference servers and models |
| LangSmith | **Passed:** previous comparison/attachment verification; physical-Windows text batch has two successful traces and a native two-run experiment. Accepted FLUX/Qwen images and YuE2 audio results have closed traces and matching decoded attachments. The initial FLUX image/recovery experiments share a dataset/case; the later fresh-prompt image pairs use different red/blue cases on their dataset, and YuE2 uses different morning/evening cases on its audio dataset | API records and attachment bytes verified; comparison UI/media rendering uninspected. The deliberately killed factory left one original outputless trace unclosed; its recovered result has a separate closed trace. Optional attachment API MIME fields were null; object headers and independent PNG/FLAC decoding passed |
| Runpod | REST lifecycle, ownership, ambiguous create recovery, startup quarantine, idle drain/stop and leader coordination tested | Configured account/template; real pod start, work, drain and stop |

Codex: [text probe](evidence/2026-10-19/codex-text.json),
[JSON probe](evidence/2026-10-19/codex-json.json). Minimal instructions replace the
supported CLI instruction layer; they do not eliminate all harness overhead. The
observed input was approximately 7.8k tokens. These tests establish one submitted
prompt/final output, not a promise about the provider's internal HTTP call count.

Ollama: [text probe](evidence/2026-10-19/ollama-text.json),
[JSON probe](evidence/2026-10-19/ollama-json.json).
OpenAI-compatible: [JSON probe](evidence/2026-10-19/openai-compatible-json.json).
LangSmith: [actual service verification](evidence/2026-10-19/langsmith.json).

## Platform verification

GitHub CI passed native Windows, macOS and Linux collector/factory tests and wheel
building, PostgreSQL integration, and a complete Linux Compose demonstration
([verified v0.1.2 run](https://github.com/slobodanmargetic988/packet-foundry/actions/runs/39335366129)).
Native CI also verifies installation from built wheels in a disposable environment,
including all five imports and four service entrypoints. Consult current workflow
results for the latest revision.

One personal Windows 10 laptop now runs the checksum-verified public v0.1.2 factory
and contracts wheels in an isolated Python 3.13 environment. Its existing llama.cpp
Vulkan runtime produced actual text and schema-validated JSON through the factory
adapter. The current-user Scheduled Task is running with limited privileges; actual
settings allow battery operation, remove the execution time limit and configure
three one-minute failure retries. HTTPS registry health, authenticated registration,
online heartbeat and the declared single-job capacity are verified. The existing
model process was not restarted, and the existing assistant peer remains healthy.

This startup check ran with the owner already logged in. A physical reboot, later
login trigger, sleep/resume and network interruption have not yet been exercised.

A real browser request for two LinkedIn drafts completed through Collector,
Warehouse, Registry and the Windows factory. Both results were imported into the
production application's Feed as **Awaiting review**, with no approval or publishing
queue entry. Collector acknowledgements and an empty Warehouse outbox were verified.
Replaying the original submission returned the same job without another batch.
Actual LangSmith records contain both successful inputs and outputs, timing and
token usage; the completed batch also exported as a native two-run experiment.

The initial 700-token output allowance produced two `incomplete_output` failures,
which created no drafts and closed their LangSmith traces with errors. A new
immutable batch using 2,048 tokens per packet succeeded; the previous packets and
model runtime were unchanged. The two successful calls used 805 and 724 completion
tokens and took approximately 29.6 and 26.1 seconds. These are functional job
observations, not a new performance benchmark. A second physical Windows computer
has now completed the separate ComfyUI image check below.

This verifies the existing llama.cpp runtime; ComfyUI, Ollama and subscription CLI
authentication have not been verified on that laptop. Windows CLI backends require
native executables; shell batch shims are rejected. Select the native executable
path in backend configuration when necessary.

### Windows ComfyUI image verification

The initial checks on a second Windows 10 factory generated two real images with
`Abiray/flux-3-image-klein-sd-GGUF:Q4_K_M` on a GTX 1060 with 6,144 MiB VRAM
(SM 6.1) and approximately 28 GiB RAM. The isolated runtime was ComfyUI 0.42.1,
Python 3.12.10, Torch 2.14.0+cu126 and pinned ComfyUI-GGUF nodes. All three model
files matched their pinned lengths and SHA-256 hashes. The operator used authorized
upstream access for the gated text encoder and VAE.

The approved graph used a CPU INT8 text encoder, CPU VAE, 512×512 canvas, four
Euler/simple steps, CFG 1 and seed 42. Visual inspection confirmed the requested
red ceramic fox scene. One packet succeeded on its first attempt; the downloaded
PNG was 303,997 bytes and matched SHA-256
`371db5a2447c2b5f09d1dbb06bf503cd7f60e92dc7f961b43a51cd5675b79d96`.
A private generic HTTP verification receiver downloaded, decoded and durably saved
the result; Warehouse accepted its acknowledgement. This image probe did not use
the Collector SDK or import an image draft into Media Presence. The linked
LangSmith attachment was independently downloaded, fully decoded at 512×512 and
matched the same bytes; its native batch experiment exported successfully. After
ACK, the receiver reused its saved result envelope, downloaded the retained artifact
again and repeated ACK harmlessly. The attempt count remained one; this checked
delivery replay, without requesting another generation.

ComfyUI reported **127.90 seconds** for this cold run, including model loading,
CPU text encoding and decoding; the factory trace lasted approximately 130 seconds.
These are functional observations, not warm throughput or a hardware benchmark.
The recorded 9.90 GiB process lifetime peak working set and a loading-time GPU/RAM
snapshot are not synchronized peak measurements; exact GPU peak was not measured.

Both current-user login tasks were installed with limited privileges and were
running during the check. Factory startup includes a local ComfyUI readiness wait.
During a second, sixteen-step generation, the factory process was stopped and
restarted in 3.217 seconds. The separate ComfyUI process continued running. The
factory resumed the same attempt, fence and provider prompt; only one provider
enqueue occurred. The accepted 512×512 PNG was downloaded, decoded, saved and ACKed,
with 308,557 bytes and SHA-256
`0ee664e78dca21d40e4ea94d91e40b0947995d9ca8feeb56af8b9f3e9c16272a`.
Its independently decoded LangSmith attachment matched. ComfyUI reported 129.42
seconds for that generation. The accepted trace lasted 113.508153 seconds starting
after restart, so its duration does not represent the whole job. The interrupted
original trace remains unclosed and has no outputs; trace count is not generation
count. Actual API checks confirmed two native experiments on the same dataset and
stable case, each with one successful run linked to the accepted result trace.
This is recovery and experiment provenance evidence, not a cold/warm or model
quality benchmark. The LangSmith comparison and media UI were not inspected.

A later consecutive pair used four steps for both requests: red fox/seed 42,
then blue fox/seed 43 with the remaining graph/settings unchanged and no intervening
model unload or process restart. Both succeeded in one accepted attempt, were
visually checked, downloaded, fully decoded, durably saved and ACKed, and matched
their closed LangSmith trace attachments. Actual native experiment API checks
verified one successful run each with expected model/profile/seed/trace/artifact
links. The cases intentionally differ for the red/blue prompts.

Server execution-start/success intervals were **275.255 seconds** after supported
model/node-cache clearing and **70.742 seconds** with model/node state preserved.
The first cached no nodes; the second cached nodes 1, 2, 3, 5 and 6, while positive
text-conditioning node 4 and sampler node 7 executed with all four steps observed.
Unchanged negative conditioning was reused. OS file caches were not cleared,
and background model downloads and other workloads remained active. These are
functional observations, not controlled cold-disk or throughput measurements.
The [Windows guide timing table](windows-comfyui.md#consecutive-four-step-flux-verification)
distinguishes server totals from node receipt intervals and rounded sampler time.

The pair's red artifact matched the earlier 303,997-byte PNG hash; actual sampler
execution was independently observed. The new blue PNG was 282,711 bytes with
SHA-256 `0010f0e7a02188e5f5b5e47ceccc15356a09182f56e959871b62a200e5609ba2`.
Both decoded at 512×512. The original 127.90-second image and sixteen-step restart
check remain separate from this matched-settings pair in the
[sanitized evidence](evidence/2026-10-20/windows-comfyui-image.json).

No fresh login, reboot, sleep/resume, robust network-interruption recovery or ComfyUI
server restart has yet been verified for this deployment. Additional image models,
reference inputs and video are still pending; YuE2 audio is verified separately below. See the [reusable Windows ComfyUI
guide](windows-comfyui.md) for pinned artifacts, example graph and scope limits.

### Qwen Image 2.1 consecutive generation verification

A real `chfm/Qwen-Image-2.1-GGUF:Q4_K_M` evaluation generated a visually checked
512×512 red ceramic fox followed by a blue fox on the same Windows/GTX 1060 runtime.
Each packet succeeded in one attempt. Both results were downloaded, decoded,
durably saved and ACKed; their matching LangSmith attachments and native batch
exports passed actual API checks. The approved graph uses a CPU
INT8 Qwen3VL 8B encoder, FP32 GGUF computation, CPU VAE, 25 Euler/simple steps and
the model's 64-channel latent output. See the
[sanitized Qwen evidence](evidence/2026-10-20/windows-qwen-image21.json).

The clean first run followed supported model unload/node-cache clearing, with no
cached nodes; OS file caches were not reset. Its server execution interval was
711.760 seconds, compared with 474.45 seconds for an earlier separate accepted
request. The second request changed red to blue in the brief and seed 42 to 43,
with the same remaining settings and no intervening model unload or process
restart. Its server execution interval was 390.925 seconds. Only loader/configuration
nodes 1, 2, 4 and 5 were cached; text-encoding node 3 and sampler node 6 executed,
with all 25 progress events observed. The two output hashes differ. Reusable
loader/model state is distinguished from exact GPU residency or cold disk. A prior local
seed-only repeat lost its lease during a network failure and is excluded from
the successful pair. These are functional checks with background activity and
observed variation, not controlled performance measurements. The
[Windows guide timing table](windows-comfyui.md#qwen-image-21-example) distinguishes
server totals from node receipt intervals and rounded sampler elapsed time.

The red PNG was 290,006 bytes with SHA-256
`dd97848645e2e689623712a935b4f93db8f5756e61fc60ef3d2b8c0c44be1bc0`;
the blue PNG was 355,836 bytes with SHA-256
`5c4eb93c6690ff6760d790a233d702b5466ffe102e3fb5e28c767a2d772860ba`.
Both fully decoded at 512×512. The two native experiments use the same dataset
with intentionally different cases because their prompts differ. Actual API checks
verified one successful run each and the accepted trace/artifact provenance.
No same-case quality comparison or LangSmith media/comparison UI inspection is
claimed. As with the FLUX checks, the receiver was a generic HTTP harness;
Collector SDK and Media Presence image import were not exercised.

The [reusable example](../examples/comfyui-qwen-image21.backend.json),
[three-file manifest](../examples/comfyui-qwen-image21.models.json) and
[workflow attribution](../examples/comfyui-qwen-image21.NOTICE.md) preserve the
reviewed configuration and sources. Qwen's model weights use the separately linked
Qwen Research terms for non-commercial research/evaluation; the Foundry code
license does not grant commercial model rights. Image editing, other Qwen
quantizations and audio/video from other model configurations remain unverified;
YuE2 audio is covered separately below.

### FLUX.1-dev consecutive generation verification

`city96/FLUX.1-dev-gguf:Q4_0` passed two consecutive 512×512 image requests through
the same physical Windows factory and generic HTTP receiver. The deployed FP32
graph uses GGUF diffusion and T5 encoders, CLIP-L, `FluxGuidance=3.5`, 20
Euler/simple steps at sampler CFG 1, and CPU VAE decode. Actual logs confirmed
CPU text-encoder load/offload and FP32 computation. All four model files matched
their pinned lengths and hashes; model licenses and workflow attribution are in
the [manifest](../examples/comfyui-flux1-dev.models.json) and
[MIT workflow notice](../examples/comfyui-flux1-dev.NOTICE.md).

Each packet succeeded in one attempt. Both PNGs were fully decoded, durably saved,
ACKed and visually checked against their red/blue fox briefs. The red image was
238,280 bytes with SHA-256
`a19d96e02bf7e61bbaf28e22741b34538f9a486d4b24050442a97c95dc689349`;
the blue image was 245,105 bytes with SHA-256
`84bd6de315b6f9458b492d38b8b8fc764f39e4d6470236f5d8aec3442b7fbc90`.
Each closed factory trace's attachment was independently decoded at 512×512 and
matched the accepted artifact. Actual native experiment checks verified one
successful run each, correct accepted trace/artifact provenance and intentionally
different cases on the same dataset. The LangSmith media/comparison UI was not
inspected.

Only positive text red→blue and seed 42→43 changed, with the same process and no
intervening model unload or cache clear. The first cached no nodes; the second
cached nodes 1, 2, 6 and 8 while text node 3 and sampler node 7 recomputed, with
all 20 steps observed. Server history intervals were **714.427 seconds** and
**556.424 seconds**; factory traces were approximately 719.670 and 560.320 seconds
with separate transport/polling boundaries. The
[Windows guide](windows-comfyui.md#flux1-dev-example) distinguishes node receipt
intervals from rounded sampler times. OS file caches were not cleared and
background model downloads/other workloads were present. The cold run's lowest
sampled available RAM was about 1.13 GiB; memory peaks were not measured
continuously. These are functional observations, not controlled performance or
hardware recommendations.

The diffusion checkpoint retains the linked FLUX.1-dev Non-Commercial model terms;
these are evaluation trials, independently of Foundry's Apache-2.0 code license.
The receiver was a generic HTTP harness, so Collector SDK and Media Presence image
import were not exercised. Other quantizations, reference-image/editing workflows,
full video and OS/model-server/network recovery remain unverified; YuE2 audio
is covered separately below. See the
[sanitized complete evidence](evidence/2026-10-20/windows-flux1-dev.json).

### YuE2 GPU audio verification

`Comfy-Org/YuE2:int8-convrot` produced two consecutive 15.0-second, 48 kHz stereo
FLACs through the same Windows factory and generic HTTP receiver. Each succeeded
in one accepted attempt, passed full decoding, durable save/ACK and exact retrieved
LangSmith attachment verification. Both user previews received usable-music
feedback. Human usability feedback is distinct from automated signal checks; lyric
accuracy, intelligibility, genre faithfulness and comparative quality remain
unassessed. PCM was finite and
nonzero with no measured near/full-scale samples, which is an indicator rather
than proof about audible distortion. LangSmith UI playback was not inspected.

The first FLAC was 974,846 bytes with SHA-256
`70e752e301520f8e848520b747a884c0bb48ab74ed1f9a8251ac0d3ece295a8a`;
the second was 648,924 bytes with SHA-256
`51bcb6456e45328647c3cdb56067eb25f637f7d9cbec13c50ded81e5207a85b1`.
Warehouse/object MIME `audio/x-flac` and detected `audio/flac` describe the same
decoded format. Optional attachment API MIME was null; exact bytes matched.
The packets omitted an exact `output.mime_type` constraint; core validation does
not normalize these aliases.
Actual API checks verified two successful native experiments on the same audio
dataset, intentionally different morning/evening cases, and accepted trace/artifact
provenance.

The eight-node graph uses stock `SelectCLIPDevice(gpu:0)` for FP32 AR with CPU
offload, existing FP32/low-VRAM diffusion and CPU VAE. Fixed original lyrics and
the 15-second cap are unchanged. Empty ABC selects the effective off mode; there
is no ABC score-planning stage. The canceled CPU trial stopped at 47/375 tokens
without full audio and is excluded from the GPU pair.

Only the style word morning→evening and both mapped seeds 42→43 changed, with no
intervening model/runtime reset. Server history totals were **696.921 seconds**
and **630.994 seconds**; closed factory traces were 700.257132 and 634.166142
seconds with different transport boundaries. The first cached no nodes; the
repeat cached loader/selector nodes 1 and 8, while AR/acoustic conditioning and
diffusion recomputed. AR terminal 375/375 and all 32 NAR sampler steps were
observed; each observer captured 188 AR progress messages, not every individual
token event. The [Windows timing table](windows-comfyui.md#yue2-gpu-audio-example)
separates server totals from combined node receipt intervals.

OS file caches were not cleared, and background downloads/other workloads were
present. Runtime retained-reference warnings and garbage collections were
preserved; two successful requests do not establish sustained leak-free memory
behavior. GPU/RAM observations are sampled, not continuous peaks or hardware
recommendations. Collector SDK audio import, an application audio UI, other audio
models and audio interruption/restart recovery remain untested. The checkpoint
retains separate CC-BY-NC-4.0 terms. See the [backend example](../examples/comfyui-yue2.backend.json),
[manifest](../examples/comfyui-yue2.models.json), [MIT attribution](../examples/comfyui-yue2.NOTICE.md)
and [sanitized evidence](evidence/2026-10-20/windows-yue2.json).

## Reproduce

```text
uv sync --locked --all-packages --all-extras --group dev
uv run --all-packages ruff check .
uv run --all-packages pytest -q
uv build --all-packages
```

Set `FOUNDRY_TEST_DATABASE_URL` and `FOUNDRY_TEST_REGISTRY_DATABASE_URL` to isolated
PostgreSQL databases before the full suite. Reliability checks skip with an explicit
message if those databases are absent. Follow [quick start](quickstart.md) to test
the actual container deployment.

Deliberate live probes use `scripts/verify_live.py`; they are excluded from normal
tests and write ignored `.local/live-evidence` files. Examples:

```text
uv run --all-packages python scripts/verify_live.py --backend codex --model YOUR_CODEX_MODEL --kind json
uv run --all-packages python scripts/verify_live.py --backend ollama --model YOUR_INSTALLED_MODEL --kind text
uv run --all-packages python scripts/verify_live.py --backend openai --model YOUR_API_MODEL --kind json --allow-api
uv run --all-packages --all-extras python scripts/verify_langsmith.py --env-file /path/to/private.env --verify-attachments
```

API probes use locally configured credentials and can incur provider charges.
Configure provider-side limits. Packets themselves have no cost policy or accounting.
The LangSmith verifier requires both isolated test PostgreSQL URLs, an already
installed Ollama model and LangSmith credentials; it creates synthetic projects and
records their private inspection URLs in the ignored local evidence file.
Full video generation, other audio/model combinations and cloud lifecycle checks
remain release verification work. The Windows text factory, two-draft application
delivery and Windows GPU image packets have been verified end to end, including
factory-only recovery during FLUX generation and consecutive fresh-prompt pairs
for FLUX.3, Qwen and FLUX.1-dev, plus two real YuE2 audio deliveries with user
usability feedback. Audio-specific interruption and OS/model-server/network recovery
and visual inspection of the LangSmith comparison UI remain pending.
