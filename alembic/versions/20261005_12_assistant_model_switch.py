"""Durable, idempotent model-session handoff references, without transcript storage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261005_12"
down_revision = "20261004_11"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistant_model_switches",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_session_id", postgresql.UUID(as_uuid=True)),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("remote_consent", sa.Boolean(), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    raise RuntimeError("Model handoff records require explicit retention reconciliation")
