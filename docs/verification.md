# Verification matrix

Status as of **20 October 2026**: working implementation, with two physical Windows
factories deployed from public release **v0.1.2** for real text/JSON and image work. The four-service
stack is exercised end to end, including real containers and PostgreSQL. Complete
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
| Physical Windows image factory | Actual ComfyUI FLUX.3 Image Klein Q4_K_M generated two visually checked 512×512 PNGs; each succeeded in one accepted attempt through Registry/Warehouse, was downloaded/ACKed, and matched its LangSmith attachment. The second survived a factory-only process restart without another provider enqueue | GTX 1060 6 GB functional checks; other models, video/audio, OS and model-server recovery remain unverified |
| Real application delivery | Browser requested two LinkedIn drafts; physical Windows factory generated both, Collector imported them, and production Feed showed both as **Awaiting review**. Exact submission replay returned the same job and no extra batch | Two actual text packets; neither draft approved or queued for publishing |

Container evidence: [deployment and restart summary](evidence/2026-10-19/compose.json).
Native Windows evidence: [installation, startup and local generation](evidence/2026-10-20/windows-native.json).
Native GPU image evidence: [real ComfyUI image delivery and trace attachment](evidence/2026-10-20/windows-comfyui-image.json).
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
| ComfyUI | **Image passed:** actual FLUX.3 Image Klein Q4_K_M on Windows/GTX 1060, approved graph, result download/checksum/ACK and matching LangSmith attachments. A factory-only restart resumed the same attempt/fence/prompt with one provider enqueue | Other models, reference-image workflows, real audio/video, model-server restart/history loss and OS/network recovery |
| OpenAI-compatible server | **Text and JSON passed** on a physical Windows laptop against existing llama.cpp Vulkan, `gemma-4-E2B-it-Q4_K_M.gguf`; JSON also passed against Ollama's compatibility endpoint, `qwen2.5-coder:0.5b` | Other compatible inference servers and models |
| LangSmith | **Passed:** previous comparison/attachment verification; physical-Windows text batch has two successful traces and a native two-run experiment. Two real GPU images have accepted closed traces, matching decoded attachments and native experiments sharing a dataset and stable case | API records and attachment bytes verified; comparison UI/media rendering uninspected. The deliberately killed factory left one original outputless trace unclosed; its recovered result has a separate closed trace. Optional attachment API MIME fields were null; object headers and PNG decoding passed |
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

A second Windows 10 factory generated two real images with
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

No fresh login, reboot, sleep/resume, network interruption or ComfyUI server restart
has yet been verified for this deployment. Other image models, reference inputs,
audio and video are still pending. See the [reusable Windows ComfyUI
guide](windows-comfyui.md) for pinned artifacts, example graph and scope limits.

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
Real audio/video generation, other model combinations and cloud lifecycle checks
remain release verification work. The Windows text factory, two-draft application
delivery and two Windows GPU image packets have been verified end to end, including
factory-only recovery during image generation. OS/model-server/network recovery
and visual inspection of the LangSmith comparison UI remain pending.
