"""Persist user-owned image styles.

Revision ID: 20260929_02
Revises: 20260923_01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260929_02"
down_revision = "20260923_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "image_styles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("positive_prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("recommended_model", sa.String(1024)),
        sa.Column("recommended_loras", postgresql.JSONB()),
        sa.Column("recommended_parameters", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_image_styles_owner_name"),
    )
    op.create_index("ix_image_styles_owner_user_id", "image_styles", ["owner_user_id"])


def downgrade():
    op.drop_index("ix_image_styles_owner_user_id", table_name="image_styles")
    op.drop_table("image_styles")
