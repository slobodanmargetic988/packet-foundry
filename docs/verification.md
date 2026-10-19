# Verification matrix

Status as of **19 October 2026**: working pre-release implementation. The four-service
stack is exercised end to end, including real containers and PostgreSQL. Complete
live verification of every media/provider integration is still pending. Do not treat
synthetic HTTP responses or mocked cloud lifecycle tests as real model/GPU evidence.

## Confirmed checks

| Check | Evidence | Boundary |
| --- | --- | --- |
| Local automated suite | **129 passed**, Python 3.12.13 on macOS ARM64, real PostgreSQL | Includes synthetic provider fixtures; no paid inference in tests |
| Mixed batch | Two actual FactoryDaemon instances processed **300 text/JSON packets**, consumed/ACKed once through Collector/Warehouse/Registry | HTTP applications in-process; inference responses synthetic |
| Docker quick start | Four separate service containers, PostgreSQL 17, both migrations, healthy servers, complete packet result and ACK | Linux ARM64 Docker; fixture provider synthetic |
| Docker recovery | Warehouse/Registry recreated and Factory/Collector restarted; second packet completed/ACKed; all pending journals empty | Same retained test volumes |
| Database isolation | Warehouse/Registry use separate roles/databases; cross-database connection refused | Compose deployment |
| Durable protocol | Duplicate submissions, shared specs, concurrent claims, capacity, lease fencing/expiry, cancellation/renewal races, bounded retries, ACK persistence | Real PostgreSQL integration tests |
| Experiment export | Two native experiments in an actual LangSmith workspace share two cases and the same dataset; four successful local generations from two factories link to their traces | Actual service API verified; comparison UI not visually inspected |
| Media trace transport | Actual SDK uploaded two synthetic PNG attachments; stored bytes were downloaded and matched | Proves transport, not model media generation or UI rendering |
| Packaging | All five wheels build; independent factory installation from wheel bundle succeeds | Native installation tested in an isolated local tool environment |

Container evidence: [deployment and restart summary](evidence/2026-10-19/compose.json).
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
| OpenAI-compatible server | **JSON passed** against Ollama's real compatibility endpoint, local `qwen2.5-coder:0.5b` | Other compatible inference servers |
| LangSmith | **Passed:** two factories, four attempt traces, two comparable native experiments, two shared cases, and two retrieved attachments | Comparison UI/media rendering not visually inspected; attachments were synthetic PNG fixtures |
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
([verified run](https://github.com/slobodanmargetic988/packet-foundry/actions/runs/39328340100)).
Native CI also verifies installation from built wheels in a disposable environment,
including all five imports and four service entrypoints. Consult current workflow
results for the latest revision. Native
managed-startup command generation is tested; installing startup services on the
owner's personal computers is deliberately deferred.

A native Windows factory does not imply GPU drivers, ComfyUI, Ollama or subscription
authentication have been installed or tested there. Windows CLI backends require
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
Real GPU/media and cloud/workspace checks remain release verification work until
actual outputs and lifecycle evidence exist; no personal Windows deployment or
Media Presence collector customization has been performed.
