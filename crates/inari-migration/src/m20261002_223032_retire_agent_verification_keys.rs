use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct Migration;

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .get_connection()
            .execute_unprepared(
                "ALTER TABLE agent_verification_keys ADD COLUMN retired_at TIMESTAMPTZ;
             UPDATE agent_verification_keys AS old SET retired_at = (
                 SELECT MIN(new.registered_at) FROM agent_verification_keys AS new
                 WHERE new.agent_id = old.agent_id AND new.purpose = old.purpose
                     AND new.registered_at > old.registered_at
             ) WHERE EXISTS (
                 SELECT 1 FROM agent_verification_keys AS new
                 WHERE new.agent_id = old.agent_id AND new.purpose = old.purpose
                     AND new.registered_at > old.registered_at
             );
             UPDATE agent_verification_keys AS key SET retired_at = CURRENT_TIMESTAMP
             WHERE key.retired_at IS NULL AND (
                 (key.purpose = 'transport_identity' AND key.key_id <> (
                     SELECT agents.key_id FROM agents WHERE agents.agent_id = key.agent_id
                 )) OR (key.purpose = 'agent_state' AND 1 < (
                     SELECT COUNT(*) FROM agent_verification_keys AS other
                     WHERE other.agent_id = key.agent_id AND other.purpose = key.purpose
                         AND other.retired_at IS NULL
                 ))
             );
             CREATE UNIQUE INDEX uq_agent_active_verification_key
                 ON agent_verification_keys (agent_id, purpose) WHERE retired_at IS NULL;",
            )
            .await?;
        Ok(())
    }
}
