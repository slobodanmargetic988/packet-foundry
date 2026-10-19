#!/usr/bin/env python3
"""Export the installed contracts' versioned JSON Schema documents."""
from __future__ import annotations

import argparse
import inspect
import json
import secrets
import tempfile
from pathlib import Path

from packet_foundry_contracts import models


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("schemas/v1"))
    parser.add_argument("--contracts-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, candidate in sorted(vars(models).items()):
        if (inspect.isclass(candidate) and issubclass(candidate, models.WireModel)
                and candidate is not models.WireModel):
            schema = candidate.model_json_schema()
            schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
            schema["$id"] = f"https://raw.githubusercontent.com/slobodanmargetic988/packet-foundry/main/schemas/v1/{name}.json"
            (args.output / f"{name}.json").write_text(
                json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
            )
            count += 1
    if not args.contracts_only:
        from packet_foundry_registry.app import create_app as registry_app
        from packet_foundry_registry.settings import RegistrySettings
        from packet_foundry_warehouse.app import create_app as warehouse_app
        from packet_foundry_warehouse.settings import WarehouseSettings

        # Construct API descriptions without opening a database or starting services.
        with tempfile.TemporaryDirectory(prefix="foundry-openapi-") as temporary:
            warehouse = warehouse_app(WarehouseSettings(
                database_url="postgresql+psycopg://localhost/foundry_docs",
                admin_token=secrets.token_urlsafe(48), service_token=secrets.token_urlsafe(48),
                artifact_path=Path(temporary),
            ))
            registry = registry_app(RegistrySettings(
                database_url="postgresql+psycopg://localhost/foundry_docs",
                admin_token=secrets.token_urlsafe(48), warehouse_token=secrets.token_urlsafe(48),
            ))
            try:
                for name, app in (("warehouse", warehouse), ("registry", registry)):
                    (args.output / f"{name}.openapi.json").write_text(
                        json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
            finally:
                warehouse.state.repository.engine.dispose()
                registry.state.engine.dispose()
    print(f"Exported {count} contracts and API descriptions to {args.output}")


if __name__ == "__main__":
    main()
