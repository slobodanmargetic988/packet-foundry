# Deployment and native factory installation

Packet Foundry has four backend services and no required frontend. Deploy all services
on one host or place native factories on separate Windows, macOS, and Linux machines.
Factories need outbound access to Registry and their configured providers only.

## Containers

The [quick start](quickstart.md) runs separate Warehouse/Registry databases on one
PostgreSQL server, applies each service's own Alembic migrations before starting it,
and persists artifacts and worker journals in independent volumes. Database roles own
only their corresponding database. Only localhost HTTP endpoints are published.

Build independently:

```text
docker build --target warehouse -t packet-foundry-warehouse:local .
docker build --target registry -t packet-foundry-registry:local .
docker build --target collector -t packet-foundry-collector:local .
docker build --target factory -t packet-foundry-factory:local .
```

The multi-target Dockerfile builds every package into wheels, then installs each
service and its shared contracts wheel using a local wheel index. Runtime dependencies
are constrained to the committed lockfile, including optional tracing dependencies. It does not assume
Packet Foundry packages already exist on PyPI. Runtime containers run as UID 10001.
Persistent data mounts must be writable by that user; configuration mounts need read
permission. Provider runtimes and GPU drivers are deliberately separate installations.

Production settings use `FOUNDRY_WAREHOUSE_*` and `FOUNDRY_REGISTRY_*` environment
variables. See package settings and the [protocol](specifications/protocol.md). Factory
and Collector accept JSON configuration containing secret references, not secret values.
Put credentials in a deployment secret store or protected mounted files where supported.
Do not use a shared admin key for applications or factories.

Apply migrations before the corresponding service starts:

```text
foundry-warehouse migrate
foundry-registry migrate
```

Database changes should be rehearsed against a restored backup. Keep database schemas
separate; services communicate through authenticated HTTP APIs, never shared tables.

## Native factories on Windows, macOS, and Linux

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Either build from source or download/extract `packet-foundry-wheels.zip` from a GitHub
release. Keep **all** wheels together so the contracts dependency resolves locally.
Verify the downloaded archive against the release's `SHA256SUMS`. The release bundle
also includes locked runtime constraints, which the bootstrap installer uses automatically.

From source:

```text
uv build --all-packages
python scripts/bootstrap.py install-factory --wheel-dir dist
```

From a downloaded wheel bundle, run the accompanying `bootstrap.py`:

```text
python bootstrap.py install-factory --wheel-dir PATH_TO_EXTRACTED_WHEELS
```

Add `--tracing` to `bootstrap.py install-factory` if this factory will use LangSmith.
The base install keeps that integration optional.

Equivalent direct installation:

```text
uv tool install --python 3.12 --find-links PATH_TO_EXTRACTED_WHEELS --constraints PATH_TO_EXTRACTED_WHEELS/constraints.txt packet-foundry-factory
```

Enroll each machine using a Registry administrator and retain its returned token.
Run setup on that machine:

```text
foundry-factory setup --config factory.json
foundry-factory doctor --config factory.json
foundry-factory doctor --config factory.json --network
foundry-factory run --config factory.json
foundry-factory status --config factory.json
```

Setup asks for provider selections and model profiles; read [Factory](factory.md) for
shared GPU capacities, approved ComfyUI workflow templates, CLI subscription login,
and optional LangSmith. Authenticate Codex/Claude using their installed CLI's supported
login flow first. Ollama/ComfyUI and their models must already be installed. Setup
performs no inference or cloud provisioning.

For an interactive session, set `FOUNDRY_FACTORY_TOKEN` to the issued token. For managed
startup, use a protected credential file and set `registry_token_env` to `null` and
`registry_token_file` to its **absolute** path. Use absolute paths for data directories,
provider key files, CLI executables, and other service-time references. The startup
service does not reliably inherit the terminal's environment or working directory.
On Windows, restrict credential files to the current user with NTFS ACLs; on Unix,
use mode 0600. Do not paste credentials into configuration examples or shell history.

```text
foundry-factory install-startup --config ABSOLUTE_PATH_TO_FACTORY_JSON
foundry-factory uninstall-startup
```

Startup runs under the current user's account: systemd user service on Linux, LaunchAgent
on macOS, and an at-logon Scheduled Task on Windows. Linux starts at login unless the
operator separately enables user lingering. Native platform execution evidence is
reported in the [verification matrix](verification.md), not inferred from generated files.

## Networking: DNS, HTTPS, Tailscale, and Headscale

Factories long-poll Registry. They do not need listening ports or inbound connectivity,
so DNS/TLS for Registry is enough for remote workers. Registry needs access to Warehouse;
Collectors need access to Warehouse. Keep PostgreSQL private. Use an HTTPS reverse
proxy for public routes with streaming enabled, long-poll timeouts above 30 seconds,
and request-size limits sized for allowed media. Avoid proxy buffering of large uploads.

For a private tailnet, install Tailscale on the host serving Registry and each factory,
use its private DNS name in `registry_url`, and restrict ACLs to factory → registry
and registry → warehouse as appropriate. A self-hosted Headscale control plane is an
alternative for operators who want to own tailnet coordination. Join devices through
their supported installation/login procedures; credentials stay outside this repository.
Do not expose factory/model-server ports or PostgreSQL publicly. A tailnet does not
replace Packet Foundry authentication. Use HTTPS or tailnet transport protections
appropriate to your network and avoid public plaintext HTTP.

Runpod workers require a Registry URL reachable from the cloud pod; a laptop-only or
localhost URL cannot work. Runpod provisioning remains opt-in and can incur charges.
The factory image defaults to `foundry-factory run`, which accepts registry-injected
`FOUNDRY_FACTORY_CONFIG_JSON` without a mounted config file. Local Compose overrides
that command with `run --config /config/factory.json`. Operator GPU images must start
their model runtime and then invoke `foundry-factory run`; do not leave a command that
requires a nonexistent `/config/factory.json`. Review [Registry](registry.md), configure explicit templates and provider-side spending
limits, and verify startup/idle-shutdown behavior before unattended use.

## Backups, retention, and operations

Back up both PostgreSQL databases, Warehouse artifact storage, worker SQLite journals,
and deployment secret/configuration files as a consistent deployment snapshot. A
Warehouse database backup without its referenced artifacts is incomplete. Protect
backups as sensitive application data. Rehearse restoring to an isolated deployment
with cloud automation disabled so recovery cannot create paid resources unexpectedly.

`/health` checks process availability; inspect authenticated packet/factory status for
actual progress. Monitor queued/live job age, lease expiry/retries, failed attempts,
offline factories, artifact disk space, and cloud pods stuck starting/stopping. Logs
must exclude tokens and sensitive input/output. LangSmith content capture is an explicit
operator choice because it transmits materials/results to a separate service.

Default retention preserves packets, results, and referenced specifications. Acknowledging
an outcome marks successful delivery; it does not delete it. Do not remove unacknowledged
results or referenced artifacts/specifications. Stop intake and drain work before
storage migration or credential rotation. Keep factory journals until remote attempt
commitments are settled.
