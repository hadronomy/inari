use std::str::FromStr;
use std::time::{Duration, Instant};

use inari_migration::{Migrator, MigratorTrait};
use sea_orm::DatabaseConnection;
use secrecy::{ExposeSecret, SecretString};
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use sqlx::{Connection, PgConnection, PgPool};

use crate::config::DatabaseConfig;
use crate::error::{AppError, AppResult};

const MIGRATION_LOCK_NAMESPACE: i32 = 0x494E_4152;
const MIGRATION_LOCK_ID: i32 = 1;
const MIGRATION_LOCK_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Clone)]
pub struct ControllerDatabase {
    pool: PgPool,
    connection: DatabaseConnection,
    connect_options: PgConnectOptions,
}

impl std::fmt::Debug for ControllerDatabase {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ControllerDatabase")
            .field("pool", &self.pool)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MigrationReport {
    pub applied: Vec<String>,
    pub pending: Vec<String>,
    pub lock_wait: Duration,
}

impl ControllerDatabase {
    pub async fn connect(config: &DatabaseConfig) -> AppResult<Self> {
        let database_url = read_database_url(config).await?;
        let connect_options = PgConnectOptions::from_str(database_url.expose_secret())
            .map_err(|source| {
                AppError::internal("database_url", "The PostgreSQL connection URL is invalid.")
                    .with_source(source)
            })?
            .application_name("inari-server");
        let pool = PgPoolOptions::new()
            .min_connections(config.min_connections)
            .max_connections(config.max_connections)
            .acquire_timeout(Duration::from_secs(10))
            .connect_with(connect_options.clone())
            .await
            .map_err(|source| {
                AppError::internal(
                    "database_connection",
                    "The controller could not connect to PostgreSQL.",
                )
                .with_source(source)
            })?;
        let connection = DatabaseConnection::from(pool.clone());
        Ok(Self { pool, connection, connect_options })
    }

    #[must_use]
    pub fn sea_orm(&self) -> &DatabaseConnection {
        &self.connection
    }

    #[must_use]
    pub fn pool(&self) -> &PgPool {
        &self.pool
    }

    pub async fn migrate(&self) -> AppResult<MigrationReport> {
        self.reject_legacy_schema().await?;
        let mut guard = PgConnection::connect_with(&self.connect_options)
            .await
            .map_err(|source| {
                AppError::internal(
                    "migration_connection",
                    "The migration lock connection could not be established.",
                )
                .with_source(source)
            })?;
        let migration_result = async {
            sqlx::query("SELECT set_config('statement_timeout', $1, false)")
                .bind(
                    MIGRATION_LOCK_TIMEOUT
                        .as_millis()
                        .to_string(),
                )
                .execute(&mut guard)
                .await
                .map_err(migration_error)?;
            let lock_started = Instant::now();
            sqlx::query("SELECT pg_advisory_lock($1, $2)")
                .bind(MIGRATION_LOCK_NAMESPACE)
                .bind(MIGRATION_LOCK_ID)
                .execute(&mut guard)
                .await
                .map_err(|source| {
                    AppError::internal(
                        "migration_lock",
                        format!(
                            "The migration lock could not be acquired within {:?}.",
                            MIGRATION_LOCK_TIMEOUT
                        ),
                    )
                    .with_source(source)
                })?;
            let lock_wait = lock_started.elapsed();
            sqlx::query("SELECT set_config('statement_timeout', '0', false)")
                .execute(&mut guard)
                .await
                .map_err(migration_error)?;
            let pending = Migrator::get_pending_migrations(&self.connection)
                .await
                .map_err(migration_error)?;
            let applied = pending
                .iter()
                .map(|migration| migration.name().to_owned())
                .collect::<Vec<_>>();
            Migrator::up(&self.connection, None)
                .await
                .map_err(migration_error)?;
            let pending = pending_migration_names(&self.connection).await?;
            Ok(MigrationReport { applied, pending, lock_wait })
        }
        .await;
        let close_result = guard
            .close()
            .await
            .map_err(migration_error);
        match migration_result {
            Ok(report) => {
                close_result?;
                Ok(report)
            },
            Err(error) => {
                let _ = close_result;
                Err(error)
            },
        }
    }

    pub async fn status(&self) -> AppResult<MigrationReport> {
        self.reject_legacy_schema().await?;
        if !self
            .relation_exists("seaql_migrations")
            .await?
        {
            return Ok(MigrationReport {
                applied: Vec::new(),
                pending: Migrator::migrations()
                    .into_iter()
                    .map(|migration| migration.name().to_owned())
                    .collect(),
                lock_wait: Duration::ZERO,
            });
        }
        Ok(MigrationReport {
            applied: Vec::new(),
            pending: pending_migration_names(&self.connection).await?,
            lock_wait: Duration::ZERO,
        })
    }

    pub async fn ensure_current(&self) -> AppResult<()> {
        let report = self.status().await?;
        if report.pending.is_empty() {
            return Ok(());
        }
        Err(AppError::internal(
            "database_migrations_pending",
            format!(
                "The controller database has {} pending migration(s); run `inari-server database migrate` before starting the service.",
                report.pending.len()
            ),
        ))
    }

    async fn reject_legacy_schema(&self) -> AppResult<()> {
        let seaorm_history = self
            .relation_exists("seaql_migrations")
            .await?;
        let sqlx_history = self
            .relation_exists("_sqlx_migrations")
            .await?;
        let controller_schema = self
            .relation_exists("organizations")
            .await?;
        if sqlx_history || (controller_schema && !seaorm_history) {
            return Err(AppError::internal(
                "legacy_database_schema",
                "This database uses the disposable alpha SQLx schema. Recreate it before applying the SeaORM baseline; no compatibility importer is provided.",
            ));
        }
        Ok(())
    }

    async fn relation_exists(&self, relation: &str) -> AppResult<bool> {
        sqlx::query_scalar::<_, bool>("SELECT to_regclass($1) IS NOT NULL")
            .bind(relation)
            .fetch_one(&self.pool)
            .await
            .map_err(|source| {
                AppError::internal(
                    "database_introspection",
                    "The controller database schema could not be inspected.",
                )
                .with_source(source)
            })
    }
}

async fn pending_migration_names(connection: &DatabaseConnection) -> AppResult<Vec<String>> {
    Ok(Migrator::get_pending_migrations(connection)
        .await
        .map_err(migration_error)?
        .into_iter()
        .map(|migration| migration.name().to_owned())
        .collect())
}

async fn read_database_url(config: &DatabaseConfig) -> AppResult<SecretString> {
    let value = tokio::fs::read_to_string(&config.url_file)
        .await
        .map_err(|source| {
            AppError::internal(
                "database_secret",
                "The PostgreSQL connection secret could not be read.",
            )
            .with_source(source)
        })?;
    Ok(SecretString::from(value.trim().to_owned()))
}

fn migration_error(source: impl std::error::Error + Send + Sync + 'static) -> AppError {
    AppError::internal("database_migration", "The controller database migration failed.")
        .with_source(source)
}
