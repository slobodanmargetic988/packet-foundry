# Verification matrix

Status as of **19 October 2026**: working pre-release implementation. The four-service
stack is exercised end to end, including real containers and PostgreSQL. Complete
live verification of every media/provider integration is still pending. Do not treat
synthetic HTTP responses or mocked cloud lifecycle tests as real model/GPU evidence.

## Confirmed checks

| Check | Evidence | Boundary |
| --- | --- | --- |
| Local automated suite | **114 passed**, Python 3.12.13 on macOS ARM64, real PostgreSQL | Includes synthetic provider fixtures; no paid inference in tests |
| Mixed batch | Two actual FactoryDaemon instances processed **300 text/JSON packets**, consumed/ACKed once through Collector/Warehouse/Registry | HTTP applications in-process; inference responses synthetic |
| Docker quick start | Four separate service containers, PostgreSQL 17, both migrations, healthy servers, complete packet result and ACK | Linux ARM64 Docker; fixture provider synthetic |
| Docker recovery | Warehouse/Registry recreated and Factory/Collector restarted; second packet completed/ACKed; all pending journals empty | Same retained test volumes |
| Database isolation | Warehouse/Registry use separate roles/databases; cross-database connection refused | Compose deployment |
| Durable protocol | Duplicate submissions, shared specs, concurrent claims, capacity, lease fencing/expiry, cancellation/renewal races, bounded retries, ACK persistence | Real PostgreSQL integration tests |
| Experiment export | Stable comparison IDs, frozen sources, terminal errors, uncertain upload reconciliation without duplicate POST | Mocked LangSmith endpoint |
| Media trace serialization | Actual optional LangSmith SDK sends MIME + attachment bytes through multipart ingestion | In-memory HTTP session; real service/UI not contacted |
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
| OpenAI-compatible server | Real adapter exercised over HTTP in Docker against a clearly synthetic fixture | Actual inference server of the operator's choice |
| LangSmith | Optional tracing/attachments and external experiment export implemented and contract-tested | Two factories writing to an actual workspace; native experiment comparison and media rendering |
| Runpod | REST lifecycle, ownership, ambiguous create recovery, startup quarantine, idle drain/stop and leader coordination tested | Configured account/template; real pod start, work, drain and stop |

Codex: [text probe](evidence/2026-10-19/codex-text.json),
[JSON probe](evidence/2026-10-19/codex-json.json). Minimal instructions replace the
supported CLI instruction layer; they do not eliminate all harness overhead. The
observed input was approximately 7.8k tokens. These tests establish one submitted
prompt/final output, not a promise about the provider's internal HTTP call count.

Ollama: [text probe](evidence/2026-10-19/ollama-text.json),
[JSON probe](evidence/2026-10-19/ollama-json.json).

## Platform verification

GitHub CI is configured for native Windows, macOS and Linux collector/factory tests,
wheel building, PostgreSQL integration, and a complete Linux Compose demonstration.
Workflow results must be consulted rather than inferred from configuration. Native
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
```

API probes use locally configured credentials and can incur provider charges.
Configure provider-side limits. Packets themselves have no cost policy or accounting.
Real GPU/media and cloud/workspace checks remain release verification work until
actual outputs and lifecycle evidence exist; no personal Windows deployment or
Media Presence collector customization has been performed.
