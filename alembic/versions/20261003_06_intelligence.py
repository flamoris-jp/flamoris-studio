"""Raw inference owner-scoped request fences, no prompt or output store."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261003_06"
down_revision = "20261003_05"
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "intelligence_requests",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, nullable=False),
        sa.Column("request_id", uid, nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "request_id", name="uq_intelligence_request_owner"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_intelligence_requests_user_id", "intelligence_requests", ["user_id"]
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM intelligence_requests)"))
        .scalar()
    ):
        raise RuntimeError(
            "Intelligence request fences must not be silently erased by downgrade"
        )
    op.drop_table("intelligence_requests")
