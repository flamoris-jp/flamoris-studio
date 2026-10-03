"""Share the durable native submission fence across Speech and Music.

Revision ID: 20261003_10
Revises: 20261003_09
"""
from alembic import op

revision = "20261003_10"
down_revision = "20261003_09"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("speech_requests", "generation_requests")


def downgrade():
    op.rename_table("generation_requests", "speech_requests")
