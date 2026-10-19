"""Real PostgreSQL ownership, persistence, migration and advisory-lock evidence."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from packet_foundry_contracts import EnrollmentCreate, ExecutionProfile, FactoryRegistration
from packet_foundry_registry import RegistrySettings, create_app
from packet_foundry_registry.database import advisory_lock, make_engine, migrate, sessions
from packet_foundry_registry.runpod import RunpodController
from packet_foundry_registry.security import enroll
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url


@pytest.fixture(scope="module")
def postgres_url():
    raw = os.environ.get("FOUNDRY_TEST_REGISTRY_DATABASE_URL")
    if not raw:
        pytest.skip("FOUNDRY_TEST_REGISTRY_DATABASE_URL is required for PostgreSQL evidence")
    schema = "registry_test_" + uuid4().hex
    admin_engine = make_engine(raw)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(raw).update_query_dict({"options": "-csearch_path=" + schema})
    value = url.render_as_string(hide_password=False)
    migrate(value)
    yield value
    with admin_engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin_engine.dispose()


def test_postgres_migration_and_factory_identity_survive_restart(postgres_url):
    migrate(postgres_url)
    engine = make_engine(postgres_url)
    assert {"registry_factories", "registry_cloud_pods", "registry_alembic_version"} <= set(
        inspect(engine).get_table_names(),
    )
    engine.dispose()
    settings = RegistrySettings(database_url=postgres_url, admin_token="a" * 32,
                                warehouse_token="s" * 32)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/v1/enrollments", json={"factory_id": "persisted", "name": "PC"},
                               headers={"Authorization": "Bearer " + "a" * 32})
        assert response.status_code == 200
        token = response.json()["token"]
    restarted = create_app(settings)
    registration = FactoryRegistration(factory_id="persisted", name="PC", profiles=[
        ExecutionProfile(profile_id="local", model="model", backend="ollama"),
    ])
    with TestClient(restarted) as client:
        response = client.post("/v1/register", json=registration.model_dump(mode="json"),
                               headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200
        listing = client.get("/v1/factories", headers={"Authorization": "Bearer " + "a" * 32})
        assert next(row for row in listing.json() if row["factory_id"] == "persisted")["online"]


def test_concurrent_postgres_enrollment_issues_only_one_credential(postgres_url):
    engine = make_engine(postgres_url)
    session_factory = sessions(engine)

    def attempt():
        with session_factory() as session:
            try:
                enroll(session, EnrollmentCreate(factory_id="concurrent", name="PC"))
                return "created"
            except ValueError:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: attempt(), range(2)))
    assert sorted(results) == ["conflict", "created"]
    engine.dispose()


def test_factory_coordination_lock_excludes_other_registry_connection(postgres_url):
    first = make_engine(postgres_url)
    second = make_engine(postgres_url)
    with advisory_lock(first, "factory:one") as owner:
        assert owner
        with advisory_lock(second, "factory:one") as other:
            assert not other
        with advisory_lock(second, "factory:another") as other_factory:
            assert other_factory
    with advisory_lock(second, "factory:one") as released:
        assert released
    first.dispose()
    second.dispose()


@pytest.mark.asyncio
async def test_only_one_postgres_reconciler_calls_provider(postgres_url):
    engine = make_engine(postgres_url)
    settings = RegistrySettings(database_url=postgres_url, admin_token="a" * 32,
                                warehouse_token="s" * 32, runpod_enabled=True,
                                public_url="https://example.test", runpod_api_key="pg-provider")
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def provider(request):
        calls.append(request.url.path)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=[])

    async def warehouse(_request):
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(base_url="http://warehouse.test", transport=httpx.MockTransport(warehouse)) as client:
        one = RunpodController(settings, engine, sessions(engine), client,
                               transport=httpx.MockTransport(provider))
        two = RunpodController(settings, engine, sessions(engine), client,
                               transport=httpx.MockTransport(provider))
        await one.open()
        await two.open()
        task = asyncio.create_task(one.tick())
        await asyncio.wait_for(entered.wait(), timeout=5)
        await asyncio.wait_for(two.tick(), timeout=1)
        assert len(calls) == 1
        release.set()
        await task
        await one.close()
        await two.close()
    engine.dispose()
