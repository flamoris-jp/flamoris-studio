"""Durable external job/asset ownership claims."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "20261003_08"
down_revision = "20261003_07"
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table("external_imports",
        sa.Column("upstream_job_id", sa.String(32), primary_key=True),
        sa.Column("user_id", uid, nullable=False),
        sa.Column("execution_id", uid, nullable=False, unique=True),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="RESTRICT"))
    op.create_index("ix_external_imports_user_id", "external_imports", ["user_id"])
    op.create_table("external_asset_claims",
        sa.Column("upstream_asset_id", sa.String(256), primary_key=True),
        sa.Column("user_id", uid, nullable=False),
        sa.Column("asset_id", uid, nullable=False, unique=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"))
    op.create_index("ix_external_asset_claims_user_id", "external_asset_claims", ["user_id"])


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM external_imports) OR EXISTS (SELECT 1 FROM external_asset_claims)"
    )).scalar():
        raise RuntimeError("External ownership claims must not be silently erased")
    op.drop_table("external_asset_claims")
    op.drop_table("external_imports")
