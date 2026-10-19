"""Factory identity, capabilities, and owned cloud lifecycle."""

import sqlalchemy as sa
from alembic import op

revision = "0001_registry"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "registry_factories",
        sa.Column("factory_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("draining", sa.Boolean(), nullable=False),
        sa.Column("registration", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "registry_identity",
        sa.Column("singleton", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False, unique=True),
    )
    op.create_table(
        "registry_cloud_pods",
        sa.Column("record_id", sa.String(36), primary_key=True),
        sa.Column("template_id", sa.String(128), nullable=False),
        sa.Column("factory_id", sa.String(128), sa.ForeignKey("registry_factories.factory_id"),
                  nullable=False, unique=True),
        sa.Column("pod_name", sa.String(200), nullable=False, unique=True),
        sa.Column("provider_pod_id", sa.String(128), nullable=True, unique=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("shutdown_action", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_busy_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
    )
    op.create_index("ix_registry_cloud_pods_template_id", "registry_cloud_pods", ["template_id"])


def downgrade():
    op.drop_table("registry_cloud_pods")
    op.drop_table("registry_identity")
    op.drop_table("registry_factories")
