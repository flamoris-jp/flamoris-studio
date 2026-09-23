"""Initial Studio identity and catalog schema (frozen historical revision).

Revision ID: 20260923_01
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260923_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(256), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_table(
        "executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("workflow", sa.String(128), nullable=False),
        sa.Column("upstream_job_id", sa.String(256)),
        sa.Column("request_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("last_known_status", sa.String(32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_executions_user_id", "executions", ["user_id"])
    op.create_table(
        "assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("upstream_asset_id", sa.String(256), nullable=False),
        sa.Column("storage_provider", sa.String(64), nullable=False),
        sa.Column("storage_locator", sa.String(256), nullable=False),
        sa.Column("storage_path", sa.Text()),
        sa.Column("original_filename", sa.String(256)),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("media_kind", sa.String(32), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("checksum", sa.String(64)),
        sa.Column("thumbnail_locator", sa.String(64)),
        sa.Column("availability", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_assets_user_id", "assets", ["user_id"])
    op.create_index("ix_assets_execution_id", "assets", ["execution_id"])
    op.create_index("ix_asset_execution_upstream", "assets", ["execution_id", "upstream_asset_id"], unique=True)


def downgrade():
    op.drop_index("ix_asset_execution_upstream", table_name="assets")
    op.drop_index("ix_assets_execution_id", table_name="assets")
    op.drop_index("ix_assets_user_id", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_executions_user_id", table_name="executions")
    op.drop_table("executions")
    op.drop_index("ix_login_sessions_user_id", table_name="login_sessions")
    op.drop_table("login_sessions")
    op.drop_table("users")
