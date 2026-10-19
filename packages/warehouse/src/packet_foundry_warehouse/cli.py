"""Warehouse administration; credentials are emitted once to the invoking terminal."""
import argparse
import json
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from packet_foundry_contracts import ApplicationCreate

from .repository import WarehouseRepository
from .settings import WarehouseSettings


def migrate(settings: WarehouseSettings):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def main():
    parser = argparse.ArgumentParser(description="Packet Foundry warehouse")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8100)
    sub.add_parser("migrate")
    app = sub.add_parser("create-application")
    app.add_argument("application_id")
    app.add_argument("--priority", type=int, default=50)
    sub.add_parser("export-experiments")
    reset = sub.add_parser("reset-experiment")
    reset.add_argument("application_id")
    reset.add_argument("batch_id")
    reset.add_argument("--confirmed-absent", action="store_true", required=True,
                       help="Confirm remote experiment absence before allowing another upload")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = WarehouseSettings()
    if args.command == "migrate":
        migrate(settings)
    elif args.command == "create-application":
        repo = WarehouseRepository(settings)
        try:
            print(json.dumps(repo.create_application(ApplicationCreate(
                application_id=args.application_id, priority=args.priority))))
        finally:
            repo.engine.dispose()
    elif args.command == "export-experiments":
        import asyncio

        from .experiments import export_completed
        repo = WarehouseRepository(settings)
        try:
            print(json.dumps(asyncio.run(export_completed(repo))))
        finally:
            repo.engine.dispose()
    elif args.command == "reset-experiment":
        repo = WarehouseRepository(settings)
        try:
            repo.reset_export(args.application_id, args.batch_id)
            print("Experiment marked pending after operator confirmation of remote absence.")
        finally:
            repo.engine.dispose()
    else:
        import uvicorn

        from .app import create_app
        uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
