# Controller database operations

The controller stores fleet state, enrollment records, audit history, and OIDC
sessions in externally managed PostgreSQL. SeaORM is the application query
layer, and `crates/inari-migration` is the only owner of schema history.

## How migrations run

Production deployments run:

```sh
inari-server database migrate
```

before controller replicas are rolled. The command takes an Inari-specific
PostgreSQL advisory lock, applies the embedded forward migrations, and reports
what changed. The Helm chart runs it in a pre-install and pre-upgrade Job.

Controller pods use `database.migrate_on_startup = false`. During readiness
they check that the database matches the binary, but they never perform DDL.
Single-process development may enable startup migration for convenience.

Check a database without changing it:

```sh
inari-server database status
```

The command exits unsuccessfully when migrations are pending, which makes it
suitable for deployment gates and incident diagnosis.

Tower Sessions uses the same PostgreSQL pool. Its `tower_sessions.session`
table belongs to Inari’s migration history, so session startup never owns a
second schema path.

## Add a migration

Generate the scaffold with the exact SeaORM CLI version used by the workspace:

```sh
cargo install sea-orm-cli --version 2.0.0-rc.42 --locked
sea-orm-cli migrate generate <concise_name> \
  --migration-dir crates/inari-migration \
  --universal-time
```

Keep the generated timestamp, module registration, and migration name. Fill in
the schema change with SeaQuery builders where they remain clear; use focused
PostgreSQL only when the schema API cannot express the operation well.

Applied migrations are immutable. Never rename, reorder, edit, or remove one.
Ship corrections as a new forward migration. SeaORM records migration names and
application times but does not calculate SQLx-style content checksums.

## Plan rolling changes

Use expand-and-contract across releases:

1. Add schema that the old and new applications can both use.
2. Roll the application and complete any explicit backfill.
3. Remove obsolete schema in a later release.

Transactional migration failures roll back automatically. Helm rollback does
not reverse schema history, so only roll back to a binary compatible with the
database already in place. Restore from the PostgreSQL backup for physical
recovery; deliver logical repair as another forward migration.

Before a migration-bearing production release, confirm a recent backup, a
tested restore path, database connectivity from the migration Job, and enough
capacity for both the migration and the rolling deployment.
