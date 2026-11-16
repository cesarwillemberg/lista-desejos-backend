from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db import sqlalchemy_url

config = context.config
config.set_main_option("sqlalchemy.url", sqlalchemy_url())


def run_migrations_offline() -> None:
    context.configure(url=sqlalchemy_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
