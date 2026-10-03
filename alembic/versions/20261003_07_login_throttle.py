"""Durable bounded multi-worker login admission."""

import sqlalchemy as sa
from alembic import op

revision = "20261003_07"
down_revision = "20261003_06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("login_throttles",
        sa.Column("address_hash", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_login_throttles_expires_at", "login_throttles", ["expires_at"])


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM login_throttles WHERE expires_at > clock_timestamp())"
    )).scalar():
        raise RuntimeError("Active login protection must not be erased by downgrade")
    op.drop_table("login_throttles")
