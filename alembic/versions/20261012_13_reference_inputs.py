"""Persist all image roles used by an execution, including uncertain submissions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261012_13"
down_revision = "20261005_12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "execution_inputs",
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("executions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(32), primary_key=True),
        sa.Column(
            "input_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("managed_inputs.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_execution_inputs_input_id", "execution_inputs", ["input_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM execution_inputs LIMIT 1")).first():
        raise RuntimeError("Reference history must be reconciled before downgrade")
    op.drop_table("execution_inputs")

