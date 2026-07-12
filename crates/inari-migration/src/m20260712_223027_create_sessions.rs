use sea_orm_migration::prelude::*;

pub struct Migration;

impl MigrationName for Migration {
    fn name(&self) -> &str {
        "m20260712_223027_create_sessions"
    }
}

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .get_connection()
            .execute_unprepared(
                r#"
                CREATE SCHEMA tower_sessions;
                CREATE TABLE tower_sessions.session (
                    id TEXT PRIMARY KEY NOT NULL,
                    data BYTEA NOT NULL,
                    expiry_date TIMESTAMPTZ NOT NULL
                );
                CREATE INDEX tower_sessions_session_expiry
                    ON tower_sessions.session (expiry_date);
                "#,
            )
            .await?;
        Ok(())
    }
}
