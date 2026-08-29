from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from inari.db.schema import configure_sqlite_dbapi_connection, metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")

    if connectable is None:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

        def configure_sqlite(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            del connection_record
            configure_sqlite_dbapi_connection(dbapi_connection)
            # Alembic batch migrations replace whole SQLite tables. SQLite
            # cannot replace a referenced table while foreign keys are live.
            # Validate the complete graph after the migration instead.
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys = OFF")
            finally:
                cursor.close()

        event.listen(connectable, "connect", configure_sqlite)

    if hasattr(connectable, "connect"):
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )

            with context.begin_transaction():
                context.run_migrations()

            violations = connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if violations:
                raise RuntimeError(
                    f"Database migration created foreign-key violations: {violations!r}"
                )
    else:
        context.configure(
            connection=connectable, target_metadata=target_metadata, compare_type=True
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
