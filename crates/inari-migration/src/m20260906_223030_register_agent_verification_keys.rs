use sea_orm_migration::{prelude::*, schema::*};

pub struct Migration;

impl MigrationName for Migration {
    fn name(&self) -> &str {
        "m20260906_223030_register_agent_verification_keys"
    }
}

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table("agent_verification_keys")
                    .col(text("key_id").primary_key())
                    .col(text("agent_id"))
                    .col(text("purpose"))
                    .col(text_uniq("jwk_thumbprint"))
                    .col(json_binary("public_jwk"))
                    .col(timestamp_with_time_zone("registered_at"))
                    .check(Expr::cust("purpose IN ('transport_identity', 'agent_state')"))
                    .foreign_key(
                        ForeignKey::create()
                            .name("agent_verification_key_owner_fk")
                            .from("agent_verification_keys", "agent_id")
                            .to("agents", "agent_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .to_owned(),
            )
            .await?;
        manager.get_connection().execute_unprepared(
            "INSERT INTO agent_verification_keys (key_id, agent_id, purpose, jwk_thumbprint, public_jwk, registered_at)
             SELECT key_id, agent_id, 'transport_identity', jwk_thumbprint, public_jwk, enrolled_at FROM agents;"
        ).await?;
        Ok(())
    }
}
