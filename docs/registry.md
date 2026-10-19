# Private registry

The registry owns factory identities, capability advertisements, heartbeats, and
optional cloud lifecycle. The warehouse owns packets, claims, leases, and accepted
outcomes. The registry never opens the warehouse database and has no second job
queue. Factories connect outward to the registry; no inbound factory port is needed.
Configured local/provider endpoints and optional LangSmith remain independent
outbound connections from a factory.

## Configure and start

Install the registry package from the workspace and provide a dedicated PostgreSQL
database. A shared PostgreSQL server with separate databases/users is sufficient.
SQLite is supported only by explicit isolated unit-test initialization, not the
service command or production coordination.

Copy `examples/registry.example.json` and edit the nonsecret settings. Supply these
environment variables through your service manager or container secret setup:

| Variable | Purpose |
| --- | --- |
| `FOUNDRY_REGISTRY_DATABASE_URL` | SQLAlchemy PostgreSQL URL, including credentials if needed |
| `FOUNDRY_REGISTRY_ADMIN_TOKEN` | High-entropy registry administration credential |
| `FOUNDRY_REGISTRY_WAREHOUSE_TOKEN` | Warehouse registry-service credential |
| `FOUNDRY_REGISTRY_RUNPOD_API_KEY` | Optional Runpod credential; used only when explicitly enabled |

All settings also accept the `FOUNDRY_REGISTRY_` environment prefix. Configuration
JSON rejects credential-value fields. Keep provider keys in environment references
and exclude environment files from version control. JSON values take precedence over
the corresponding environment values; omit a JSON field when you want an environment
variable to control it. Admin and warehouse-service credentials must be distinct
and each at least 32 characters; generate them randomly rather than using passwords.

```sh
foundry-registry --config registry.json migrate
foundry-registry --config registry.json run
```

Schema migrations are explicit; startup does not silently create or upgrade tables.
The default listener is `127.0.0.1:8102`. Put remote deployments behind HTTPS or a
private authenticated network. Runpod factories require `public_url` reachable from
their containers; a private tailnet URL works only when the operator image joins
that network. A service DNS name in a local Compose network is not a public URL.

## Enroll and operate factories

```sh
foundry-registry --url https://registry.example.com enroll windows-one --name "Windows laptop one"
foundry-registry --url https://registry.example.com list
foundry-registry --url https://registry.example.com rotate windows-one
foundry-registry --url https://registry.example.com revoke windows-one
```

Enrollment and rotation print a new opaque credential once. Store it as the
factory's configured secret reference. Only its SHA-256 digest is persisted; list
responses contain no credentials. Every credential is restricted to one factory.
Duplicate enrollment returns a conflict rather than reissuing an existing token.
Rotation invalidates the old credential immediately and requires fresh registration
before requesting work. Revocation blocks registration, heartbeats, and all relays.
Rotation preserves an operator/cloud drain marker; it does not silently reactivate a
drained machine. For managed cloud factories, recreate the pod or update its runtime
secret as well: rotating a database digest does not update the provider environment.

Factories register all execution profiles and resource-group capacities, then send
heartbeats. A factory becomes offline after 90 seconds by default. Offline,
unregistered, revoked, and draining factories cannot obtain assignments. In-flight
attempts still use warehouse lease and fencing checks. The registry forwards all
eligible profiles in one claim so their JSON order cannot override packet priority.
`POST /v1/work?wait_seconds=25` polls without prefetching; clients can use zero for an
immediate attempt. Disconnection ends the polling loop.

Attempt endpoints verify the factory identity in the typed body. Artifact and
attempt-read endpoints inject the authenticated factory identity and forward the
fencing token. Artifact uploads/downloads stream directly between the caller and
warehouse; the registry neither reads the whole body nor keeps a local media copy.
Warehouse errors are returned as status codes with sanitized details. Outgoing
clients close when the application shuts down; interrupted downloads close their
upstream response.
JSON manifests are limited to 4 MiB by default (`max_manifest_bytes`), including
unauthenticated requests; streamed media uploads are governed by warehouse artifact
limits instead.

`GET /health` is anonymous database readiness: it checks a database query and returns
503 if the registry database is unavailable. It does not probe model/cloud providers.
Administration and worker endpoints require their respective credentials. Interactive
documentation routes are disabled; the repository protocol is the API specification.

## Opt-in Runpod factories

Set `runpod_enabled` to true, supply the API key through the environment, configure a
reachable `public_url`, and add templates. The disabled example illustrates the
configuration; its operator image placeholder must be replaced before enabling it.
The image must contain Packet Foundry factory and the advertised model runtime,
models/workflows, and an entrypoint that starts the necessary services followed by
`foundry-factory run`. The registry does not install GPU drivers or download models
inside an arbitrary provider template.

Each template defines advertised profiles, resource groups, a nonsecret
`factory_config` containing configured backends, permitted Runpod creation parameters,
maximum concurrent factories, startup/idle timeouts, and `stop` or `terminate` shutdown.
These are functional settings. There is no packet cost policy, budget, accounting,
or application quota. Provider/account limits remain the operator's responsibility.

`environment` contains nonsecret literal configuration. `environment_from` maps
container environment variable names to registry-process environment variable names;
use it for provider/LangSmith keys. Bootstrap factory identity, registry URL,
credential reference, and the unique token are injected by the registry. Those
bootstrap values cannot be overridden with provider creation parameters.

The lifecycle reconciler calls Runpod's documented REST API:
`GET/POST /v1/pods`, `POST /v1/pods/{id}/start`,
`POST /v1/pods/{id}/stop`, and `DELETE /v1/pods/{id}`.
[Runpod API reference](https://docs.runpod.io/api-reference/pods/POST/pods)

Only queued, eligible, compatible **nonzero-priority** work starts/resumes capacity.
Priority-zero experiments never start cloud resources. Live requests receive the
next compatible free slot; there is no reservation or preemption. Cloud startup can
therefore be slower than an already-running local factory.

Creation first persists an unpredictable ownership name, factory identity, and
credential digest. If the provider create response is lost, later inventory polls
match that name rather than issuing a duplicate create. Ownership and pod state
survive registry restarts. A PostgreSQL advisory leader lock prevents two registry
processes provisioning simultaneously. Factory-specific locks serialize a claim
with draining so a pod cannot be stopped while a claim is crossing the relay.

The reconciler stops only persisted owned pods with matching ownership names and no
active warehouse attempts. Idle shutdown marks the factory draining before the
provider call. Retained stopped pods resume before new ones are created. Removing
or disabling a template drains its existing owned pods. A startup timeout stops and
quarantines a retained pod instead of repeatedly restarting a failing image.
If a previously running factory stops heartbeating, queued demand cannot keep it
alive indefinitely: after all valid warehouse leases end, the reconciler stops and
quarantines it as `offline_failed`. This failure path retains the pod for diagnosis,
even if normal idle shutdown is configured to terminate. It never aborts an active
valid lease or automatically creates an endless series of broken replacements.
Disabling the entire Runpod integration stops API reconciliation; drain or shut down
managed resources first. Stopping a provider pod can retain storage; consult Runpod's
resource lifecycle documentation for the behavior of your volume setup.

```sh
foundry-registry --url https://registry.example.com pods
foundry-registry --url https://registry.example.com recover-pod RECORD_ID
```

Recovery resumes a provider-confirmed stopped owned pod, including one quarantined
after a startup or heartbeat failure. Fix its runtime configuration before resuming. Unknown
create outcomes remain visible as `create_unknown` and intentionally block replacement
capacity. If the provider confirms no pod exists, explicitly acknowledge that check:

```sh
foundry-registry --url https://registry.example.com recover-pod RECORD_ID --confirm-provider-absence
```

This abandons the unknown attempt and revokes its factory credential, allowing new
capacity. The ownership record remains under observation: if a delayed pod creation
later appears, it can still be recognized and shut down. The registry never adopts
or stops unrelated pods merely because they use the same template or GPU type.

## Verification

Registry tests cover identity isolation, hashing/rotation/revocation, offline and
draining workers, all-profile forwarding, fencing headers, stream transport, error
redaction, and mocked Runpod creation/reconciliation/start/stop/recovery. Tests with
`FOUNDRY_TEST_REGISTRY_DATABASE_URL` additionally verify actual PostgreSQL migrations,
restart persistence, concurrent enrollment, and distributed advisory-lock exclusion.
Mocked provider tests do not constitute a real Runpod GPU deployment or model smoke
test. The cloud integration makes no paid calls during normal tests.
