# Packet Foundry

Reusable, private infrastructure for one-request generation of text, structured JSON,
images, audio, and video. Python 3.12+, Apache-2.0, backend only.

```text
Application ↔ Collector ↔ Warehouse ↔ Registry ↔ Factories
```

Collectors adapt application materials into versioned packets and import outcomes.
Warehouse owns durable work, media, leases, and results. Registry authenticates and
routes to compatible factories. Factories connect outward to Registry, invoke one
configured provider, and return one result package. Each service is a separate Python
package and container; Warehouse and Registry own separate PostgreSQL databases.

## Start here

- [Cost-free full-stack quick start](docs/quickstart.md)
- [Collector SDK and application adapter guide](docs/collector.md)
- [Warehouse storage, delivery, and experiment export](docs/warehouse.md)
- [Registry and Runpod operation](docs/registry.md)
- [Factory setup and supported providers](docs/factory.md)
- [Deployment, native installation, networking, and backups](docs/deployment.md)
- [Wire protocol and reliability contract](docs/specifications/protocol.md)
- [Verification matrix and remaining real-provider checks](docs/verification.md)

This is an initial implementation. Automated tests, synthetic provider fixtures, and
real paid-provider/hardware verification are distinct evidence. Consult the matrix
before relying on a particular backend or platform in production. A successful demo
proves the packet delivery loop; its synthetic provider performs no model inference.

## Design

Packets contain pinned shared specifications, materials/artifact references, generation
instructions, expected output, priority/live scheduling flags, and optional experiment
metadata. They carry no billing, budgets, or provider credentials. Provider choice and
permitted models belong to each factory's configuration.

- Live work takes the next compatible free slot; running work is never preempted.
- Normal work combines application and packet priority, deadlines, and submission order.
- Priority-zero experiments use idle capacity only and do not wake cloud factories.
- Atomic claims and fenced leases permit at-least-once execution with one accepted result.
- Idempotent submissions, journaled worker recovery, and consume-before-ACK prevent lost work.
- Optional LangSmith traces/experiments and Runpod startup/shutdown are explicitly configured.

Provider adapters cover OpenAI, Anthropic, Ollama, OpenAI-compatible servers, Codex CLI,
Claude CLI, Google image/video, OpenAI speech, and approved ComfyUI templates. Actual
readiness depends on prerequisites and the [verification matrix](docs/verification.md).
No public registry, conversational agent orchestration, frontend, or application-specific
Media Presence integration is included.

## Development

```text
uv sync --locked --all-packages --group dev
uv run --all-packages pytest packages/collector/tests packages/factory/tests -q
uv run --all-packages ruff check .
uv build --all-packages
uv run --all-packages python scripts/export_schemas.py --output schemas/v1
```

PostgreSQL reliability tests require isolated test databases; see [Contributing](CONTRIBUTING.md).
CI runs native tests on Windows, macOS, and Linux, PostgreSQL integration tests on Linux,
and the cost-free Compose demo. Provider tests with fake HTTP responses remain synthetic.

[License](LICENSE) · [Security](SECURITY.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)
