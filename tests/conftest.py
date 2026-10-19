import os
from uuid import uuid4

import pytest
from packet_foundry_warehouse.repository import WarehouseRepository
from packet_foundry_warehouse.settings import WarehouseSettings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture
def warehouse_settings(tmp_path):
    database_url = os.environ.get("FOUNDRY_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set FOUNDRY_TEST_DATABASE_URL to an isolated PostgreSQL test database")
    engine = create_engine(database_url)
    if engine.dialect.name != "postgresql":
        pytest.fail("reliability integration tests require PostgreSQL")
    schema = "test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    settings = WarehouseSettings(database_url=url.render_as_string(hide_password=False),
        admin_token="a" * 32, service_token="s" * 32, artifact_path=tmp_path / "artifacts")
    yield settings
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


@pytest.fixture
def repository(warehouse_settings):
    repo = WarehouseRepository(warehouse_settings)
    repo.initialize()
    yield repo
    repo.engine.dispose()
