# Controller database lifecycle

The production controller uses externally managed PostgreSQL. The application
query layer is SeaORM 2 and the embedded schema history lives in the dedicated
`inari-migration` crate.

## Ownership

There is one schema owner:

- `inari-server database migrate` acquires the Inari PostgreSQL advisory lock
  and applies the embedded SeaORM migrations.
- The Helm pre-install and pre-upgrade Job invokes that command before
  controller replicas are rolled.
- Production pods set `database.migrate_on_startup = false`. They verify that
  no migration is pending before becoming ready, but never perform DDL.
- Development may set `database.migrate_on_startup = true` for a single local
  process.

Tower Sessions remains the session implementation and uses its upstream
PostgreSQL store. Its `tower_sessions.session` table is created by the same
embedded migration history; application startup never invokes the store's
independent migration helper.

## Commands

Apply all pending migrations:

```sh
inari-server database migrate
```

Verify that a database is current without applying pending migrations:

```sh
inari-server database status
```

The status command exits unsuccessfully when migrations are pending, making it
suitable for deployment validation.

## Adding a migration

Generate migration scaffolding with the exact workspace SeaORM version:

```sh
cargo install sea-orm-cli --version 2.0.0-rc.42 --locked
sea-orm-cli migrate generate <concise_name> \
  --migration-dir crates/inari-migration \
  --universal-time
```

The generator owns the timestamped filename, module registration, migration
name, and trait scaffold. The engineer then implements and reviews the schema
change inside that scaffold; generated DDL is never accepted without curation.

Use SeaQuery schema builders for ordinary tables, indexes, constraints, and
foreign keys. Use raw PostgreSQL statements only for a capability that the
schema API does not express clearly. Register migrations in chronological
order and keep each module focused on one cohesive change.

Applied migrations are immutable. Never edit, rename, reorder, or remove one;
add a new forward migration instead. SeaORM records migration names and
application timestamps, but it does not provide SQLx-style content checksums.
Inari does not pretend otherwise or maintain a parallel checksum engine.

## Rollout and recovery

Future rolling changes follow expand-and-contract discipline:

1. Add schema that both the old and new binaries can use.
2. Roll the application and backfill through explicit operational work.
3. Remove obsolete schema in a later release after no running binary depends
   on it.

Database migrations are forward-only in the production CLI. A failed
transactional migration rolls back automatically. Recovery from a destructive
operator or infrastructure failure uses the managed PostgreSQL backup, while
logical corrections are delivered as a new forward migration. Helm rollback
does not attempt to reverse database history.

The former `_sqlx_migrations` alpha schema is disposable. The SeaORM runner
rejects it instead of silently baselining or importing it; recreate those
development databases before upgrading.
