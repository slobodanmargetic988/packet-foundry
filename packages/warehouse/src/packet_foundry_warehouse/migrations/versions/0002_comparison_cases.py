"""Freeze source inputs for stable comparison case identities."""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("comparison_cases",
        sa.Column("application_id", sa.String(128), sa.ForeignKey("applications.application_id"), primary_key=True),
        sa.Column("dataset_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), primary_key=True),
        sa.Column("source_hash", sa.String(64), nullable=False))


def downgrade():
    op.drop_table("comparison_cases")
