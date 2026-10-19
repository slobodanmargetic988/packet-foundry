# Cost-free full-stack quick start

Prerequisites: Python 3.12+ and Docker Engine/Desktop with Compose v2. Run from the
repository root. No provider credentials, GPUs, paid APIs, or Runpod accounts are used.
The example provider is explicitly synthetic and returns a deterministic response.

```text
python scripts/bootstrap.py init
docker compose --env-file .local/demo/stack.env up --build --wait postgres warehouse registry
python scripts/bootstrap.py enroll
docker compose --env-file .local/demo/stack.env --profile workers --profile demo up --build --wait
python scripts/demo.py
```

`init` creates ignored `.local/demo` state with independently generated database/admin/
service credentials, nonsecret container configuration, and empty worker credential
files. It preserves existing credentials and configuration on subsequent runs. `enroll`
waits for the running services and stores newly issued application/factory credentials
privately. Only the worker's own scoped credential is passed to each worker container.

The demonstration writes a manifest into the Collector's input directory. Collector
packages and submits it, Registry routes it, Factory invokes the synthetic HTTP
provider, and Collector imports/acknowledges the result. The printed JSON confirms a
successful, acknowledged packet and explicitly marks the result synthetic. It does
not establish real-provider or GPU support.

Warehouse is at `http://127.0.0.1:8101` and Registry at `http://127.0.0.1:8102`.
Each serves `/health`; interactive documentation routes are disabled. Export versioned
JSON Schema and OpenAPI files with `uv run --all-packages python scripts/export_schemas.py`.
The published ports bind only to localhost. PostgreSQL is internal to the Compose network.

To inspect committed results, substitute the directory hash in the path:

```text
docker compose --env-file .local/demo/stack.env exec collector ls /data/output
```

View status/logs and stop without deleting data:

```text
docker compose --env-file .local/demo/stack.env --profile workers --profile demo ps
docker compose --env-file .local/demo/stack.env --profile workers --profile demo logs --tail 100
docker compose --env-file .local/demo/stack.env --profile workers --profile demo down
```

Persist `.local/demo`, PostgreSQL volumes, Warehouse artifact volume, and worker journals.
Changing database credentials in `stack.env` does not rotate existing PostgreSQL roles;
the init script runs only when the database volume is new. Do not delete volumes to
solve credential issues on a deployment containing valuable data.

If enrollment reports an existing identity with a missing local credential, restore
that credential from backup or rotate/recreate that identity administratively. Retrying
must not overwrite an existing credential or silently replace its authorization.

## Switch to a real provider

Stop Factory, edit `.local/demo/factory.json` to select a real backend/model, and make
the corresponding key available through an environment reference in `factory.env` or
a protected mounted credential file. Read [Factory](factory.md) first. Update packets'
model requirement to match the advertised profile. Restart Factory and use `doctor`
to verify prerequisites. Runpod and LangSmith remain disabled until explicitly configured.

Do not expose the synthetic provider outside the private demo network. Real provider
calls can incur charges: configure spending limits with the provider account. Packets
do not contain spending policies.
