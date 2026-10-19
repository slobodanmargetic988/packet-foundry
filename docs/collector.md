# Collector: application packaging and delivery

The collector is an application-side SDK and scheduled process. It prepares packets,
submits them to Warehouse, consumes completed outcomes, and acknowledges only after
application consumption has committed. It contains no application-specific prompts.

Install Python 3.12+ and `packet-foundry-collector`, or use the repository workspace
with `uv run --package packet-foundry-collector foundry-collector`. Run:

```text
foundry-collector init --config collector.json
foundry-collector send --config collector.json
foundry-collector collect --config collector.json
foundry-collector run --config collector.json
foundry-collector status --config collector.json
foundry-collector seal-batch tomorrow --config collector.json
foundry-collector wait PACKET_ID --config collector.json
```

Register the application using the Warehouse admin API first. Set the issued token
in the environment variable named by `token_env`; configuration files contain only
the variable name. The collector never enrolls itself with an admin credential.
Use HTTPS outside a trusted local network. Relative filesystem paths in the config
resolve against its directory. See `examples/collector.example.json`.

## File adapter

Place a manifest in `input/*.json`. Inline text/JSON and existing specification/artifact
references use the shared Packet schema. The `packet` object can omit `application_id`,
`packet_id`, `created_at`, and `idempotency_key`; the collector supplies them durably.
Explicit application IDs must match its configuration.

```json
{
  "packet": {
    "idempotency_key": "example-article-v1",
    "batch_id": "tomorrow",
    "priority": 50,
    "request": {"instructions": "Summarize the reference in three sentences."},
    "output": {"kind": "text"},
    "materials": [{"kind": "text", "name": "reference", "text": "Source material"}]
  },
  "files": [
    {"path": "reference.pdf", "kind": "document", "name": "reference", "mime_type": "application/pdf"}
  ],
  "specification_documents": [
    {"spec_id": "voice", "version": "1", "instructions": "Use clear, direct language."}
  ]
}
```

Material files must resolve inside the input directory. Absolute paths, traversal,
and symlinks to outside files are refused. File bytes are streamed and verified by
SHA-256. Specification documents are registered once remotely as immutable versions;
their returned references append in declared order after any existing references.
The packet pins those exact versions. Existing output `schema_ref` values can also
point at registered immutable schema specifications.

Unchanged manifests are sent once even across restarts; changing a manifest makes a
new source. If you change content under an explicit idempotency key, Warehouse rejects
it: use a new key to request new work. Do not edit binary files while preparation is
in progress. Removing a source after staging does not cancel its pending submission.
A failed preparation before staging may upload an unused artifact; it does not submit
partial work. Keep the SQLite journal volume persistent when running containers.

Results appear atomically as `output/<sha256(packet_id)>/result.json` together with
verified artifacts. The JSON contains the complete outcome, packet ID, and portable
local artifact filenames. Failed and cancelled outcomes are delivered too. Repeated
consumption verifies the committed result and its artifacts rather than writing it
again. Do not delete committed outputs before the collector acknowledges them.

## Delivery and scheduling guarantees

The SQLite journal persists the exact packet manifest **before** submission. A lost
response retries the same packet ID and idempotency key, allowing Warehouse to return
the already accepted packet. A successful application commit is journaled before ACK.
ACK failure is retried without consuming again. A crash between an application commit
and the local consumed marker can repeat `consume`; application consumers therefore
must be idempotent by packet ID. This cannot be solved by two unrelated databases
without an application transaction/idempotency key. The file adapter meets this rule
with its atomic directory commit.

`send_schedule` and `collect_schedule` independently accept exactly one of
`interval_seconds` or a five-field `cron`, plus an IANA `timezone`. Both run immediately
on first startup. Successful runs persist their next due time. On restart, an overdue
schedule runs once and advances from completion time, coalescing all missed runs.
Failures remain due; the other schedule still runs. Cron skips nonexistent spring
wall times and runs at both occurrences of an ambiguous autumn wall time. Intervals
use elapsed UTC time. Reconfiguration starts that schedule immediately.

Run one scheduled process per journal. A local SQLite journal should not be shared
across machines or on network filesystems. Separate collector instances should use
separate journals and durable, application-level idempotency keys.

A live application can call `Collector.submit(packet)` and `Collector.wait(packet_id)`.
The wait returns a terminal `PacketRecord` without acknowledging it. Application
consumption still goes through `collect` or the application's equivalent idempotent
commit/ACK path. Batch sealing is explicit and should happen after all submissions
succeed; new packets cannot be added to a sealed batch.

## Custom adapter template

Implement these three async methods. Return stable source keys, build a shared Packet,
and commit consumption using the packet ID as an application transaction key:

```python
from packet_foundry_collector import Collector, Journal, Source, WarehouseClient

class ApplicationAdapter:
    async def sources(self):
        return [Source(key=record.stable_key, value=record) for record in await app.pending()]

    async def prepare(self, source, client):
        # client.upload_artifact(path, mime_type)
        # client.register_specification(specification)
        return build_packet(source.value)

    async def consume(self, envelope, client):
        # Download artifacts before the application commit if required.
        # Inside one app transaction: check envelope.packet.packet_id, write the
        # outcome, and mark that packet ID imported. Return after commit.
        await app.import_outcome_once(envelope.packet.packet_id, envelope)

async def deliver(config, adapter):
    journal = Journal(config.journal)
    try:
        async with WarehouseClient(config.warehouse_url, config.token()) as client:
            collector = Collector(client, journal, adapter)
            await collector.send()
            await collector.collect()
    finally:
        journal.close()
```

For CLI integration, set `adapter_factory` to a trusted import path such as
`my_application.collector:make_adapter`. The callable receives `CollectorConfig` and
returns your adapter. This runs operator-controlled Python code. Keep application
secrets in environment variables or an external secret manager, never in packets,
source metadata, example files, or error logs.
