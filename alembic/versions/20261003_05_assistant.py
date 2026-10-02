"""Owner-scoped Agent references and durable request fences, no transcripts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261003_05"
down_revision = "20261001_04"
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "assistant_sessions",
        sa.Column("user_id", uid, primary_key=True),
        sa.Column("id", uid, nullable=False, unique=True),
        sa.Column("upstream_session_id", uid, nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "assistant_requests",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, nullable=False),
        sa.Column("request_id", uid, nullable=False),
        sa.Column("session_id", uid, nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("upstream_conversation_id", uid),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "request_id", name="uq_assistant_request_owner"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_assistant_requests_user_id", "assistant_requests", ["user_id"])


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM assistant_requests) "
            "OR EXISTS (SELECT 1 FROM assistant_sessions)"
        )
    ).scalar():
        raise RuntimeError(
            "Assistant request fences must not be silently erased by downgrade"
        )
    op.drop_table("assistant_requests")
    op.drop_table("assistant_sessions")
