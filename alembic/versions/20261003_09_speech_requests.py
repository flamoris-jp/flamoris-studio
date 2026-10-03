"""Owner-scoped durable Speech request replay fences."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "20261003_09"
down_revision = "20261003_08"
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table("speech_requests",
        sa.Column("user_id", uid, primary_key=True),
        sa.Column("request_id", uid, primary_key=True),
        sa.Column("execution_id", uid, nullable=False, unique=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="RESTRICT"))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM speech_requests)")).scalar():
        raise RuntimeError("Speech request replay fences must not be silently erased")
    op.drop_table("speech_requests")
