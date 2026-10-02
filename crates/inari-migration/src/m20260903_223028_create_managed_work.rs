use sea_orm_migration::{prelude::*, schema::*};

pub struct Migration;

impl MigrationName for Migration {
    fn name(&self) -> &str {
        "m20260903_223028_create_managed_work"
    }
}

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .alter_table(
                Table::alter()
                    .table("agents")
                    .add_column(json_binary_null("dispatch_key"))
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("managed_work_preflights")
                    .col(text("preflight_id").primary_key())
                    .col(json_binary("request"))
                    .col(text("dispatch_key_id"))
                    .col(text("capability_digest"))
                    .col(timestamp_with_time_zone("work_expires_at"))
                    .col(timestamp_with_time_zone("idempotency_expires_at"))
                    .col(timestamp_with_time_zone("submit_before"))
                    .col(timestamp_with_time_zone("created_at"))
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("managed_work")
                    .col(text("managed_work_id").primary_key())
                    .col(text("preflight_id"))
                    .col(text("organization_id"))
                    .col(text("database_name"))
                    .col(text("company_id"))
                    .col(text("site_id"))
                    .col(text("agent_id"))
                    .col(text("device_id"))
                    .col(text_uniq("print_intent_id"))
                    .col(text("operation"))
                    .col(text("media_type"))
                    .col(text("state"))
                    .col(json_binary("binding_claim"))
                    .col(binary("payload_fingerprint"))
                    .col(binary("request_fingerprint"))
                    .col(json_binary("sealed_document"))
                    .col(big_integer("payload_bytes"))
                    .col(text_null("print_job_id"))
                    .col(text_null("error_code"))
                    .col(text("message_key"))
                    .col(timestamp_with_time_zone("expires_at"))
                    .col(timestamp_with_time_zone("idempotency_expires_at"))
                    .col(timestamp_with_time_zone("admitted_at"))
                    .col(timestamp_with_time_zone("updated_at"))
                    .check(Expr::cust("operation IN ('report_pdf', 'label_document')"))
                    .check(Expr::cust(
                        "state IN ('pending_agent', 'dispatching', 'accepted', 'rejected', 'canceled', 'expired', 'recovery_uncertain')",
                    ))
                    .check(Expr::cust("octet_length(payload_fingerprint) = 32"))
                    .check(Expr::cust("octet_length(request_fingerprint) = 32"))
                    .check(Expr::cust("payload_bytes > 0"))
                    .foreign_key(
                        ForeignKey::create()
                            .name("managed_work_preflight_fk")
                            .from("managed_work", "preflight_id")
                            .to("managed_work_preflights", "preflight_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("managed_work_organization_fk")
                            .from("managed_work", "organization_id")
                            .to("organizations", "organization_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("managed_work_site_fk")
                            .from("managed_work", "site_id")
                            .to("sites", "site_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("managed_work_agent_fk")
                            .from("managed_work", "agent_id")
                            .to("agents", "agent_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("managed_work_device_fk")
                            .from("managed_work", "device_id")
                            .to("devices", "device_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("managed_work_agent_state")
                    .table("managed_work")
                    .col("agent_id")
                    .col("state")
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("managed_work_organization_state")
                    .table("managed_work")
                    .col("organization_id")
                    .col("state")
                    .to_owned(),
            )
            .await?;
        Ok(())
    }
}
