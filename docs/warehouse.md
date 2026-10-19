# Warehouse operations

The warehouse is the sole authority for packets, attempts, leases, artifacts, and
accepted results. Run its migrations before starting the service. Production
coordination requires PostgreSQL; isolated SQLite helpers do not implement its locks.

## Configuration

Install the warehouse wheel and matching contracts wheel, then set:

| Environment variable | Meaning |
| --- | --- |
| `FOUNDRY_WAREHOUSE_DATABASE_URL` | Dedicated PostgreSQL database/user URL |
| `FOUNDRY_WAREHOUSE_ADMIN_TOKEN` | High-entropy administration credential, at least 32 characters |
| `FOUNDRY_WAREHOUSE_SERVICE_TOKEN` | Different registry-service credential, at least 32 characters |
| `FOUNDRY_WAREHOUSE_ARTIFACT_PATH` | Persistent, private artifact volume |
| `FOUNDRY_WAREHOUSE_LEASE_SECONDS` | Lease lifetime; default 120 seconds |
| `FOUNDRY_WAREHOUSE_MAX_ATTEMPTS` | Maximum attempts; default 3 |

```text
foundry-warehouse migrate
foundry-warehouse run --host 127.0.0.1 --port 8101
foundry-warehouse create-application my-application --priority 70
```

Application creation prints its credential once. Store it privately in the
collector's configured environment/file reference. Only a digest is stored in the
warehouse. Administrative HTTP endpoints can rotate or revoke application
credentials; rotation invalidates the previous credential immediately. Secrets do
not belong in manifests or URLs. Public listeners require HTTPS or a protected
private network; the supplied Compose configuration publishes only localhost.

`GET /health` checks database connectivity. Interactive documentation routes are
disabled; versioned OpenAPI/JSON Schemas are in `schemas/v1`.

## Submission, priority, and delivery

Application-scoped idempotency keys identify logical submissions. Retrying equivalent
content returns the original packet, even after a batch is sealed. Different content
under the same key returns 409. Generated packet IDs and creation timestamps are not
part of the idempotency content hash. Reuse the key deliberately; a new request needs
a new key.

Instruction/schema specifications are immutable per application, identifier, and
version. The warehouse validates every pinned hash and artifact reference before
accepting a packet. Attempts reference the original packet and resolve pinned
specifications when needed; they do not duplicate shared instruction bundles in
the database. JSON Schemas use draft 2020-12, local references only. Invalid or
unresolved references return a validation error rather than triggering network fetches.

Factories advertise every eligible profile together. The warehouse accounts for
nonexpired leases per shared resource group, matches operation/model/input/output
requirements, and atomically claims the highest eligible packet. Live work precedes
normal work; normal work combines application and packet priority, then deadlines
and FIFO. Priority-zero experiments run only after eligible production work and only
on accepting profiles. No running job is preempted, and deadlines are not promises.
Continuous higher-priority work can delay lower-priority requests.

Expired leases are recovered periodically, even when factories are offline. Fencing
prevents a former owner from overwriting another attempt's result. Retryable failures
use bounded exponential delay. Three failed/expired attempts produce a terminal
failure by default. Provider execution can happen more than once after ambiguous
network failures; only one outcome is accepted.

Outbox reads do not consume results. The collector commits application changes,
then acknowledges delivery. Acknowledgement hides the result from pending-delivery
queries but retains the packet, outcome, and artifacts. Cancelled/failed packets are
also terminal deliverable outcomes. No automatic content deletion is enabled.

## Artifacts and backup

Upload raw bytes with Content-Type and X-Content-SHA256. Streaming uploads are stored
temporarily, size-checked, checksum-checked, and committed only after the full file
arrives. Rejected/interrupted transfers remove temporary files. A process killed
during an upload can leave a `.partial` file; an operator may remove old incomplete
files after confirming no corresponding transfer is active. Never delete committed
files based only on an outbox acknowledgement.

Back up PostgreSQL and the artifact volume together, retaining references between
them. Use the same protection as source material. Restore to an isolated instance
before switching applications/registry to it; workers from an old deployment must
not be allowed to commit against a restored queue without validating their leases.

## Optional native LangSmith experiments

Factory tracing and warehouse experiment export are separate opt-ins. Factories
trace attempts immediately; the warehouse exports only sealed, fully terminal
batches, including failures, without generating anything again.

Set `FOUNDRY_WAREHOUSE_LANGSMITH_API_KEY` and
`FOUNDRY_WAREHOUSE_LANGSMITH_EXPORT=true`. Optional endpoint/workspace settings are
`FOUNDRY_WAREHOUSE_LANGSMITH_ENDPOINT` and
`FOUNDRY_WAREHOUSE_LANGSMITH_WORKSPACE_ID`. The same exporter can run once with:

```text
foundry-warehouse export-experiments
```

Every packet in a comparison batch must provide experiment metadata with one common
dataset ID and a distinct case ID. Reuse those dataset/case identities across batches
to align comparisons. Source material is immutable for a given dataset/case; change
the case ID when source content changes. Prompt/model variants belong to experiment
run metadata, so changing a prompt does not break comparison identity. Seal a batch
only after all submissions are acknowledged.

The exporter uses LangSmith's external experiment upload protocol. Its dataset IDs
are independent of datasets created through other LangSmith APIs. Artifact manifests
and factory trace IDs are retained; binary comparison-grid rendering is not supplied
by that JSON upload. Factory content-capture tracing uploads supported media
attachments separately. Inspect media through those traces and the collector's
downloaded outputs.

Export state is durable and visible in batch status. An uncertain upload is reconciled
by unique experiment name and manifest hash before any resubmission. If the remote
experiment cannot be found, it remains uncertain rather than risking duplicates.
After independently confirming remote absence, an operator can permit a retry:

```text
foundry-warehouse reset-experiment my-application my-batch --confirmed-absent
foundry-warehouse export-experiments
```

Export errors never alter generation or collection outcomes. Store returned experiment
identities/links with the batch; real comparison UI and media rendering must be
verified against the configured LangSmith workspace.
