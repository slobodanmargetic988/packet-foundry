"""Support configured artifact sizes above PostgreSQL's signed 32-bit integer limit."""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("artifacts", "size_bytes", existing_type=sa.Integer(), type_=sa.BigInteger())


def downgrade():
    op.alter_column("artifacts", "size_bytes", existing_type=sa.BigInteger(), type_=sa.Integer())
