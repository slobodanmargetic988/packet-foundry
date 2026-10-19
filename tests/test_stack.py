"""All four services, real PostgreSQL, synthetic inference over an actual adapter.

These tests prove delivery/routing, not quality or real provider availability.
"""
import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from packet_foundry_collector import Collector, FileAdapter, Journal, WarehouseClient
from packet_foundry_contracts import ExecutionProfile, FactoryRegistration
from packet_foundry_factory.config import BackendConfig, FactoryConfig
from packet_foundry_factory.daemon import FactoryDaemon
from packet_foundry_factory.protocol import RegistryClient
from packet_foundry_factory.providers import Provider
from packet_foundry_registry.app import create_app as create_registry
from packet_foundry_registry.settings import RegistrySettings
from packet_foundry_warehouse.app import create_app as create_warehouse
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture
def registry_database():
    url = os.environ.get("FOUNDRY_TEST_REGISTRY_DATABASE_URL", os.environ.get("FOUNDRY_TEST_DATABASE_URL"))
    if not url:
        pytest.skip("PostgreSQL integration database is not configured")
    engine = create_engine(url)
    schema = "registry_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    yield make_url(url).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


async def test_300_packets_two_factories_complete_and_collect(warehouse_settings, registry_database, tmp_path):
    warehouse = create_warehouse(warehouse_settings, initialize_schema=True)
    registry = create_registry(RegistrySettings(database_url=registry_database,
        admin_token="r" * 32, warehouse_token="s" * 32, warehouse_url="http://warehouse",
        poll_interval_seconds=0.01), warehouse_transport=httpx.ASGITransport(app=warehouse),
        initialize_schema=True)
    calls = {"text-model": 0, "json-model": 0}

    async def inference(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        model = body["model"]
        calls[model] += 1
        await asyncio.sleep(0.001)
        content = json.dumps({"value": "synthetic"}) if "response_format" in body else "Synthetic output."
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                     "message": {"content": content}}]})

    def provider(backend, max_bytes):
        return Provider(backend, max_bytes,
            client=httpx.AsyncClient(transport=httpx.MockTransport(inference)))

    async with warehouse.router.lifespan_context(warehouse), registry.router.lifespan_context(registry):
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=warehouse), base_url="http://warehouse") as wh,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=registry), base_url="http://registry") as rg,
        ):
            issued = await wh.post("/v1/applications", headers={"Authorization": "Bearer " + "a" * 32},
                                   json={"application_id": "demo"})
            issued.raise_for_status()
            client = WarehouseClient("http://warehouse", issued.json()["token"], client=wh)
            sources, outputs = tmp_path / "input", tmp_path / "output"
            sources.mkdir()
            for index in range(300):
                kind = "text" if index % 2 == 0 else "json"
                output = {"kind": kind}
                if kind == "json":
                    output["json_schema"] = {"type": "object", "properties": {"value": {"type": "string"}},
                                              "required": ["value"], "additionalProperties": False}
                (sources / f"{index:03}.json").write_text(json.dumps({"packet": {
                    "batch_id": "mixed-300", "idempotency_key": str(index),
                    "request": {"instructions": "Return a short result.", "model": kind + "-model"},
                    "output": output}}))
            journal = Journal(tmp_path / "collector.sqlite3")
            collector = Collector(client, journal, FileAdapter(sources, outputs, "demo"))
            assert await collector.send() == 300
            assert await collector.send() == 0
            await collector.seal_batch("mixed-300")
            daemons, tasks = [], []
            for kind in ("text", "json"):
                factory_id = "factory-" + kind
                enrolled = await rg.post("/v1/enrollments", json={"factory_id": factory_id, "name": factory_id},
                    headers={"Authorization": "Bearer " + "r" * 32})
                enrolled.raise_for_status()
                credential_file = tmp_path / (factory_id + ".token")
                credential_file.write_text(enrolled.json()["token"])
                config = FactoryConfig(registry_url="http://registry", registry_token_env=None,
                    registry_token_file=credential_file,
                    registration=FactoryRegistration(factory_id=factory_id, name=factory_id,
                        profiles=[ExecutionProfile(profile_id=kind, model=kind + "-model",
                                                   backend="compat", output_kinds=[kind])]),
                    backends={"compat": BackendConfig(kind="openai_compatible",
                        base_url="http://synthetic/v1", auth_mode="none")},
                    data_dir=tmp_path / factory_id, reconnect_seconds=0.01)
                connection = httpx.AsyncClient(transport=httpx.ASGITransport(app=registry),
                    base_url="http://registry/", headers={"Authorization": "Bearer " + config.token()})
                daemon = FactoryDaemon(config, registry=RegistryClient(config, connection),
                                       provider_factory=provider)
                daemons.append(daemon)
                tasks.append(asyncio.create_task(daemon.run()))
            try:
                async with asyncio.timeout(90):
                    while (await client.batch("mixed-300"))["terminal"] != 300:
                        for task in tasks:
                            if task.done():
                                task.result()
                        await asyncio.sleep(0.05)
                assert calls == {"text-model": 150, "json-model": 150}
                assert await collector.collect(limit=1000, batch_id="mixed-300") == 300
                assert await collector.collect(limit=1000) == 0
                files = list(outputs.glob("*/result.json"))
                assert len(files) == 300
                assert all(json.loads(path.read_text())["envelope"]["status"] == "succeeded" for path in files)
            finally:
                for daemon in daemons:
                    daemon.stop.set()
                await asyncio.wait_for(asyncio.gather(*tasks), 10)
                journal.close()
