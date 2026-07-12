use sea_orm_migration::{prelude::*, schema::*};

pub struct Migration;

impl MigrationName for Migration {
    fn name(&self) -> &str {
        "m20260712_223026_create_gateway_data"
    }
}

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table("commands")
                    .col(text("command_id").primary_key())
                    .col(text("agent_id"))
                    .col(text_uniq("message_id"))
                    .col(big_integer("sequence"))
                    .col(text("state"))
                    .col(json_binary("command"))
                    .col(binary("request_fingerprint"))
                    .col(timestamp_with_time_zone("issued_at"))
                    .col(timestamp_with_time_zone_null("published_at"))
                    .col(timestamp_with_time_zone("updated_at"))
                    .check(Expr::cust("sequence > 0"))
                    .check(Expr::cust("octet_length(request_fingerprint) = 32"))
                    .foreign_key(
                        ForeignKey::create()
                            .name("commands_agent_fk")
                            .from("commands", "agent_id")
                            .to("agents", "agent_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .index(
                        Index::create()
                            .name("commands_agent_sequence_key")
                            .col("agent_id")
                            .col("sequence")
                            .unique(),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("publications")
                    .col(text("message_id").primary_key())
                    .col(text("agent_id"))
                    .col(text("key_expr"))
                    .col(text_null("message_type"))
                    .col(json_binary("payload"))
                    .col(timestamp_with_time_zone("received_at"))
                    .foreign_key(
                        ForeignKey::create()
                            .name("publications_agent_fk")
                            .from("publications", "agent_id")
                            .to("agents", "agent_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("publications_agent_received")
                    .table("publications")
                    .col("agent_id")
                    .col(("received_at", IndexOrder::Desc))
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("publications_agent_type_received")
                    .table("publications")
                    .col("agent_id")
                    .col("message_type")
                    .col(("received_at", IndexOrder::Desc))
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("audit_events")
                    .col(
                        big_integer("event_id")
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(text("organization_id"))
                    .col(text("actor_id"))
                    .col(text("action"))
                    .col(text("resource_kind"))
                    .col(text_null("resource_id"))
                    .col(text("outcome"))
                    .col(text_null("request_id"))
                    .col(json_binary("detail").default(Expr::cust("'{}'::JSONB")))
                    .col(timestamp_with_time_zone("occurred_at").default(Expr::current_timestamp()))
                    .foreign_key(
                        ForeignKey::create()
                            .name("audit_events_organization_fk")
                            .from("audit_events", "organization_id")
                            .to("organizations", "organization_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("audit_events_organization_time")
                    .table("audit_events")
                    .col("organization_id")
                    .col(("occurred_at", IndexOrder::Desc))
                    .to_owned(),
            )
            .await?;
        Ok(())
    }
}
