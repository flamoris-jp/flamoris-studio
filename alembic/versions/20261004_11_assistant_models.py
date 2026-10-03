"""Persist session model references without owning Agent settings."""

from alembic import op
import sqlalchemy as sa

revision = "20261004_11"
down_revision = "20261003_10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "assistant_sessions", sa.Column("model_id", sa.String(128), nullable=True)
    )
    op.add_column(
        "assistant_sessions",
        sa.Column(
            "remote_consent", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )


def downgrade():
    op.drop_column("assistant_sessions", "remote_consent")
    op.drop_column("assistant_sessions", "model_id")
