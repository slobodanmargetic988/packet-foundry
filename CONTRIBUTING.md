# Contributing

Use Python 3.12+ and uv. Read `AGENTS.md` and the wire protocol before changing a shared
contract. Keep service storage and credentials isolated and adapters application-neutral.

```text
uv sync --locked --all-packages --group dev
uv run --all-packages ruff check .
uv run --all-packages pytest packages/collector/tests packages/factory/tests -q
uv build --all-packages
```

For reliability tests, create two **disposable** PostgreSQL databases and set
`FOUNDRY_TEST_DATABASE_URL` and `FOUNDRY_TEST_REGISTRY_DATABASE_URL` to their SQLAlchemy
`postgresql+psycopg://...` URLs. Tests create isolated schemas and must never target a
production database. Run `uv run --all-packages pytest -q`. PostgreSQL tests skipped
because no database was configured are verification gaps, not passing integration tests.
Registry SQLite tests cover isolated logic only and do not establish concurrent routing.

Run `python scripts/bootstrap.py init` and follow the quick start to exercise the complete
container loop with its explicit synthetic provider. Do not invoke paid providers or
Runpod while running ordinary tests. Never commit real secrets or private materials.

Add focused tests for changed behavior, especially retries, stale leases, idempotency,
application isolation, cross-platform subprocess handling, and artifact integrity. Update
protocol/schema documentation for public interface changes and publish verification
evidence honestly. Provider HTTP fixtures do not prove real model inference.

All contributions use Apache-2.0. Include appropriate notices for any code copied from
another project. An architectural reference is not copied source; do not add fictional
license notices. Keep dependencies declared in their owning package and refresh `uv.lock`.
