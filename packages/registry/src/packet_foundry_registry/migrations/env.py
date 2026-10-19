from alembic import context
from packet_foundry_registry.database import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use foundry-registry migrate to supply the registry database connection")
context.configure(connection=connection, target_metadata=Base.metadata,
                  version_table="registry_alembic_version")
with context.begin_transaction():
    context.run_migrations()
