"""Command-line setup, durable delivery, and independent scheduling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from .client import WarehouseClient
from .collector import Collector
from .config import CollectorConfig
from .journal import Journal
from .scheduler import Scheduler

app = typer.Typer(help="Package generation requests and safely collect completed outcomes.")


def _execute(config_path: Path, command: str, *, value: str | None = None) -> None:
    async def execute() -> object:
        config = CollectorConfig.load(config_path)
        journal = Journal(config.journal)
        try:
            if command == "status":
                return journal.status()
            async with WarehouseClient(config.warehouse_url, config.token()) as client:
                collector = Collector(client, journal, config.adapter())
                if command == "send":
                    return {"submitted": await collector.send()}
                if command == "collect":
                    return {"acknowledged": await collector.collect()}
                if command == "seal":
                    await collector.seal_batch(value)
                    return {"sealed": True, "batch_id": value}
                if command == "wait":
                    return (await collector.wait(value)).model_dump(mode="json")
                await Scheduler(
                    journal,
                    {
                        "send": (config.send_schedule, collector.send),
                        "collect": (config.collect_schedule, collector.collect),
                    },
                ).run()
        finally:
            journal.close()

    try:
        result = asyncio.run(execute())
        if result is not None:
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        raise typer.Exit(0) from None
    except Exception as exc:
        # Configuration validation hides input; network/app exceptions can contain
        # secrets, so report only their category and provide no HTTP dump.
        if isinstance(exc, ValueError) and str(exc).startswith("set the "):
            typer.echo(str(exc), err=True)
        else:
            typer.echo(f"Collector failed ({type(exc).__name__}).", err=True)
        raise typer.Exit(1) from None


@app.command()
def init(config: Path = Path("collector.json")) -> None:
    """Create a configuration through a short setup questionnaire."""
    if config.exists():
        raise typer.BadParameter("configuration already exists")
    configured = CollectorConfig(
        application_id=typer.prompt("Application ID"),
        warehouse_url=typer.prompt("Warehouse URL", default="http://127.0.0.1:8000"),
        token_env=typer.prompt("Application token environment variable", default="FOUNDRY_APPLICATION_TOKEN"),
    )
    config.parent.mkdir(parents=True, exist_ok=True)
    with config.open("x", encoding="utf-8") as stream:
        stream.write(configured.model_dump_json(indent=2) + "\n")
    typer.echo(f"Created {config}; set {configured.token_env} before sending or collecting.")


@app.command()
def send(config: Path = Path("collector.json")) -> None:
    """Prepare inputs and submit/retry pending packets once."""
    _execute(config, "send")


@app.command()
def collect(config: Path = Path("collector.json")) -> None:
    """Consume completed outcomes and retry pending acknowledgements once."""
    _execute(config, "collect")


@app.command()
def run(config: Path = Path("collector.json")) -> None:
    """Run independently configured send and collection schedules."""
    _execute(config, "run")


@app.command()
def status(config: Path = Path("collector.json")) -> None:
    """Show local delivery counts without contacting the warehouse."""
    _execute(config, "status")


@app.command("seal-batch")
def seal_batch(batch_id: str, config: Path = Path("collector.json")) -> None:
    """Seal a batch after its packets are submitted."""
    _execute(config, "seal", value=batch_id)


@app.command()
def wait(packet_id: str, config: Path = Path("collector.json")) -> None:
    """Wait up to five minutes for an outcome; collection performs consumption/ACK."""
    _execute(config, "wait", value=packet_id)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
