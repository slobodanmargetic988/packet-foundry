# Packet Foundry

Independent Python backend services; no application-specific generation or frontend.
Read docs/specifications/protocol.md before changing a shared interface.
Warehouse alone owns durable jobs, leases, and accepted outcomes. Registry alone owns
factory identities and cloud lifecycle. Never read another service's database.
Factories connect outward to registry only among Foundry services.
No cost policy, quotas, or billing in packets. Provider choice belongs to factory config.
Keep secrets out of logs, fixtures, public commits, and packet manifests.
Core reliability requires tests against PostgreSQL, not SQLite substitutes.
Publish evidence honestly: mocked integration tests are not real provider verification.
Do not run paid/cloud integrations unless deliberately enabled and configured.
