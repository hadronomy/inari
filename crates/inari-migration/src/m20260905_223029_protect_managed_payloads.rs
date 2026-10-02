use sea_orm_migration::{prelude::*, schema::*};

pub struct Migration;

impl MigrationName for Migration {
    fn name(&self) -> &str {
        "m20260905_223029_protect_managed_payloads"
    }
}

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager.get_connection().execute_unprepared(
            "LOCK TABLE commands, managed_work IN ACCESS EXCLUSIVE MODE;
             DO $$ BEGIN
                 IF EXISTS (SELECT 1 FROM managed_work
                     WHERE state IN ('pending_agent', 'dispatching') AND expires_at > CURRENT_TIMESTAMP)
                 THEN RAISE EXCEPTION 'Managed Work is still pending: stop new admissions and let existing work finish or expire before upgrading';
                 END IF;
             END $$;
             ALTER TABLE managed_work ADD COLUMN idempotency_key TEXT;
             ALTER TABLE managed_work ADD COLUMN payload_deleted_at TIMESTAMPTZ;
             UPDATE managed_work AS work
             SET idempotency_key = delivery.command #>> '{payload,authenticated_data,idempotency_key}'
             FROM commands AS delivery
             WHERE delivery.command #>> '{payload,managed_work_id}' = work.managed_work_id;
             ALTER TABLE managed_work ALTER COLUMN idempotency_key SET NOT NULL;
             CREATE UNIQUE INDEX managed_work_organization_idempotency ON managed_work (organization_id, idempotency_key);
             UPDATE managed_work SET
                 state = CASE state WHEN 'pending_agent' THEN 'expired' WHEN 'dispatching' THEN 'recovery_uncertain' ELSE state END,
                 message_key = CASE state WHEN 'pending_agent' THEN 'managed_work.expired' WHEN 'dispatching' THEN 'managed_work.recovery_uncertain' ELSE message_key END,
                 payload_deleted_at = CURRENT_TIMESTAMP,
                 updated_at = CURRENT_TIMESTAMP;
             UPDATE commands SET command = jsonb_build_object('managed_work_id', command #>> '{payload,managed_work_id}')
             WHERE command ->> 'type' = 'controller.command.dispatch_device_work';
             ALTER TABLE managed_work DROP COLUMN sealed_document;"
        ).await?;
        manager
            .create_table(
                Table::create()
                    .table("managed_payloads")
                    .col(text("managed_work_id").primary_key())
                    .col(binary("ciphertext"))
                    .col(binary("nonce"))
                    .col(text("wrapped_data_key"))
                    .col(integer("wrapping_key_version"))
                    .col(binary("authenticated_data_digest"))
                    .col(binary("payload_fingerprint"))
                    .col(big_integer("plaintext_bytes"))
                    .col(timestamp_with_time_zone("expires_at"))
                    .col(timestamp_with_time_zone("created_at"))
                    .check(Expr::cust("octet_length(nonce) = 12"))
                    .check(Expr::cust("octet_length(authenticated_data_digest) = 32"))
                    .check(Expr::cust("octet_length(payload_fingerprint) = 32"))
                    .check(Expr::cust("plaintext_bytes > 0"))
                    .check(Expr::cust("wrapping_key_version > 0"))
                    .foreign_key(
                        ForeignKey::create()
                            .name("managed_payload_work_fk")
                            .from("managed_payloads", "managed_work_id")
                            .to("managed_work", "managed_work_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;
        Ok(())
    }
}
