# Verification matrix

Status as of **20 October 2026**: working implementation, with a physical Windows
factory deployed from public release **v0.1.2**. The four-service
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
| Real application delivery | Browser requested two LinkedIn drafts; physical Windows factory generated both, Collector imported them, and production Feed showed both as **Awaiting review**. Exact submission replay returned the same job and no extra batch | Two actual text packets; neither draft approved or queued for publishing |

Container evidence: [deployment and restart summary](evidence/2026-10-19/compose.json).
Native Windows evidence: [installation, startup and local generation](evidence/2026-10-20/windows-native.json).
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
| ComfyUI | Approved templates, shared prompts, file handling, traversal rejection and operation recovery tested | Actual installed models/workflows and image/video generation on GPU |
| OpenAI-compatible server | **Text and JSON passed** on a physical Windows laptop against existing llama.cpp Vulkan, `gemma-4-E2B-it-Q4_K_M.gguf`; JSON also passed against Ollama's compatibility endpoint, `qwen2.5-coder:0.5b` | Other compatible inference servers and models |
| LangSmith | **Passed:** previous two-factory comparison/attachment verification; physical-Windows application batch has two successful input/output traces, two earlier closed failure traces, and a completed-batch native experiment with two runs | Actual API records verified; comparison UI/media rendering not visually inspected; attachments were synthetic PNG fixtures |
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
observations, not a new performance benchmark. A second personal computer has not
been deployed.

This verifies the existing llama.cpp runtime; ComfyUI, Ollama and subscription CLI
authentication have not been verified on that laptop. Windows CLI backends require
native executables; shell batch shims are rejected. Select the native executable
path in backend configuration when necessary.

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
Real image/audio/video generation and cloud lifecycle checks remain release
verification work until actual outputs and lifecycle evidence exist. The Windows
text factory and Media Presence's two-draft delivery have been verified end to end.
Physical recovery checks and visual inspection of the LangSmith comparison UI
remain pending.
