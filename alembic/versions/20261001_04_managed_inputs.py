"""Owned immutable input mappings with bounded thumbnail accounting."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261001_04"
down_revision = "20260929_03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "managed_inputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_asset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("upstream_input_id", sa.String(64), unique=True),
        sa.Column("source_upstream_id", sa.String(256)),
        sa.Column("checksum", sa.String(64)),
        sa.Column("mime_type", sa.String(64)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("thumbnail_locator", sa.String(64)),
        sa.Column("accounted_bytes", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_asset_id"], ["assets.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_managed_inputs_owner_user_id", "managed_inputs", ["owner_user_id"]
    )
    op.create_index("ix_managed_inputs_expires_at", "managed_inputs", ["expires_at"])
    op.create_index("ix_managed_inputs_terminal_at", "managed_inputs", ["terminal_at"])
    op.create_index("ix_managed_input_cleanup", "managed_inputs", ["terminal_at", "id"])
    op.add_column(
        "executions", sa.Column("reference_input_id", postgresql.UUID(as_uuid=True))
    )
    op.create_foreign_key(
        "fk_execution_reference_input",
        "executions",
        "managed_inputs",
        ["reference_input_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_executions_reference_input_id", "executions", ["reference_input_id"]
    )


def downgrade():
    # Operator must reconcile active references before a paired application rollback.
    op.drop_index("ix_executions_reference_input_id", table_name="executions")
    op.drop_constraint("fk_execution_reference_input", "executions", type_="foreignkey")
    op.drop_column("executions", "reference_input_id")
    op.drop_table("managed_inputs")
