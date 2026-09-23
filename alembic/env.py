import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from flamoris_studio.db import Base

config = context.config
target_metadata = Base.metadata
dsn = os.environ.get("STUDIO_DATABASE_URL")
if not dsn:
    raise RuntimeError("STUDIO_DATABASE_URL must be configured")
config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))


def run_migrations_offline():
    context.configure(url=dsn, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(dsn, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
