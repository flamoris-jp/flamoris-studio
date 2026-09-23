"""Initial Studio identity and catalog schema.

Revision ID: 20260923_01
Revises:
"""
from alembic import op

from flamoris_studio.db import Base

revision = "20260923_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Use the same declarative metadata Alembic compares during future revisions.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
