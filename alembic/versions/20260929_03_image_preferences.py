"""Persist user-specific image editor defaults.

Revision ID: 20260929_03
Revises: 20260929_02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260929_03"
down_revision = "20260929_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "image_preferences",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("cfg", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade():
    op.drop_table("image_preferences")
