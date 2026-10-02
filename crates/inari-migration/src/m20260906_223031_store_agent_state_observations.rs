use sea_orm_migration::{prelude::*, schema::*};

pub struct Migration;

impl MigrationName for Migration {
    fn name(&self) -> &str {
        "m20260906_223031_store_agent_state_observations"
    }
}

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager.get_connection().execute_unprepared(
            "LOCK TABLE commands, managed_work, managed_payloads IN ACCESS EXCLUSIVE MODE;
             DO $$ BEGIN
                 IF EXISTS (SELECT 1 FROM managed_work
                     WHERE state IN ('pending_agent', 'dispatching') AND expires_at > CURRENT_TIMESTAMP)
                 THEN RAISE EXCEPTION 'Managed Work is still pending: stop new admissions and let existing work finish or expire before upgrading the Device Work fingerprint';
                 END IF;
             END $$;
             UPDATE managed_work SET
                 state = CASE state WHEN 'pending_agent' THEN 'expired' WHEN 'dispatching' THEN 'recovery_uncertain' ELSE state END,
                 message_key = CASE state WHEN 'pending_agent' THEN 'managed_work.expired' WHEN 'dispatching' THEN 'managed_work.recovery_uncertain' ELSE message_key END,
                 updated_at = CURRENT_TIMESTAMP
             WHERE state IN ('pending_agent', 'dispatching');
             UPDATE managed_work SET payload_deleted_at = CURRENT_TIMESTAMP
             WHERE managed_work_id IN (SELECT managed_work_id FROM managed_payloads)
                 AND payload_deleted_at IS NULL;
             DELETE FROM managed_payloads;"
        ).await?;
        manager
            .alter_table(
                Table::alter()
                    .table("managed_work")
                    .add_column(json_binary_null("print_job_observation"))
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("uq_managed_work_agent_print_job")
                    .table("managed_work")
                    .col("agent_id")
                    .col("print_job_id")
                    .unique()
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("agent_state_observations")
                    .col(text("envelope_id").primary_key())
                    .col(text("agent_id"))
                    .col(text("managed_work_id"))
                    .col(json_binary("observation"))
                    .col(timestamp_with_time_zone("received_at"))
                    .foreign_key(
                        ForeignKey::create()
                            .name("agent_state_observation_work_fk")
                            .from("agent_state_observations", "managed_work_id")
                            .to("managed_work", "managed_work_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("agent_state_observation_agent_fk")
                            .from("agent_state_observations", "agent_id")
                            .to("agents", "agent_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("idx_agent_state_observation_work")
                    .table("agent_state_observations")
                    .col("managed_work_id")
                    .col("received_at")
                    .to_owned(),
            )
            .await?;
        Ok(())
    }
}
