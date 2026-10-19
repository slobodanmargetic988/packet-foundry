# Packet Foundry v1 protocol

## Functional specification

Collectors submit one generation request per packet and consume one durable outcome.
Warehouse retains inputs/results independently of applications. Private registries
route compatible work to outbound-connected factories. Provider policy is local to
factories. No public registry, autonomous agent workflows, or application integration.

## Technical breakdown

All JSON types are defined in packet_foundry_contracts. IDs are strings. All times
are UTC-aware RFC3339. All endpoints below have /v1 prefix and Bearer authentication.
Only GET /health is anonymous. JSON errors use {detail: string}. Secrets never appear
in responses except newly issued credentials, once.

### Warehouse

Admin token: POST /applications {application_id, priority=50} -> {application_id, token}.
Application token: POST /specifications Specification -> SpecificationRef;
POST /artifacts raw bytes with X-Content-SHA256 and Content-Type -> ArtifactRef;
GET /artifacts/{artifact_id} -> bytes;
POST /packets Packet -> PacketRecord (202, replay 200);
GET /packets/{packet_id} -> PacketRecord;
POST /packets/{packet_id}/cancel -> PacketRecord;
GET /results?limit=100&batch_id=... -> list[ResultEnvelope];
POST /results/{packet_id}/ack -> {acknowledged: true};
POST /batches/{batch_id}/seal -> {sealed: true};
GET /batches/{batch_id} -> {batch_id, sealed, total, terminal}.
Sealed batches cannot receive more packets, except idempotent retries.

Registry service token: POST /internal/claims ClaimRequest -> Assignment | null;
POST /internal/attempts/{attempt_id}/renew AttemptToken -> {lease_expires_at};
POST /internal/attempts/{attempt_id}/complete Completion -> PacketRecord;
POST /internal/attempts/{attempt_id}/fail Failure -> PacketRecord;
GET /internal/attempts/{attempt_id} -> Assignment (only while current);
GET /internal/demand -> list[Packet] (queued and eligible, no claims).
GET /internal/artifacts/{artifact_id}?attempt_id=... -> bytes (attempt input only);
POST /internal/artifacts?attempt_id=... raw bytes -> ArtifactRef (active attempt).
Every internal attempt route also carries factory_id and fencing_token in the typed
body (or X-Factory-ID and X-Fencing-Token headers on artifact/get routes).

### Registry

Admin token: POST /enrollments {factory_id, name} -> {factory_id, token};
GET /factories -> records without secrets.
Factory token: POST /register FactoryRegistration -> {registered: true};
POST /heartbeat -> {online: true};
POST /work?wait_seconds=25 -> Assignment | null;
POST /attempts/{attempt_id}/renew AttemptToken;
POST /attempts/{attempt_id}/complete Completion;
POST /attempts/{attempt_id}/fail Failure;
GET /attempts/{attempt_id} with X-Fencing-Token -> Assignment;
GET /artifacts/{artifact_id}?attempt_id=... with X-Fencing-Token -> bytes;
POST /artifacts?attempt_id=... with X-Fencing-Token and X-Content-SHA256 -> ArtifactRef.
Registry injects/verifies factory_id, forwards to warehouse, streams bytes without
buffering whole artifacts. Registration body factory_id must match token identity.
Factory must register before requesting work. Capabilities update on registration.

### Scheduling and recovery

ClaimRequest contains all eligible profiles and resource group capacities for ONE
factory. Warehouse serializes claims per factory, accounts active nonexpired leases
per resource group, matches exact model (if requested), operation/output kind,
live support, input kinds and optional workflow template. It picks the highest job
across ALL profiles, rather than letting profile ordering defeat job priority.
Rank: live first; then normal with app priority + packet priority; deadline; FIFO.
Priority zero is idle-only, requires profile accept_experiments and live=false.
Leases default 120s, renew every 30s. Default maximum attempts 3, with backoff for
retryable errors. Cancel/expired attempts cannot renew or commit. A replay of the
same accepted completion is safe, different completion conflicts. One accepted
outcome, at-least-once execution. No prefetch or preemption.

Specifications are immutable: instruction blocks are appended in ref order, then
packet instructions. output.schema_ref pins an immutable specification JSON schema.
Artifact refs/spec refs must exist, be owned by the application, and match checksums.
Unacknowledged terminal results remain fetchable. Acknowledgement does not delete.
Collector consumers must be idempotent by packet ID. Factory journals provider
operation IDs before polling and spools outputs until server acknowledgement.
