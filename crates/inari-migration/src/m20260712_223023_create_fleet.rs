use sea_orm_migration::{prelude::*, schema::*};

pub struct Migration;

impl MigrationName for Migration {
    fn name(&self) -> &str {
        "m20260712_223023_create_fleet"
    }
}

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table("organizations")
                    .col(text("organization_id").primary_key())
                    .col(text("name"))
                    .col(timestamp_with_time_zone("created_at").default(Expr::current_timestamp()))
                    .col(timestamp_with_time_zone("updated_at").default(Expr::current_timestamp()))
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("sites")
                    .col(text("site_id").primary_key())
                    .col(text("organization_id"))
                    .col(text("name"))
                    .col(timestamp_with_time_zone("created_at").default(Expr::current_timestamp()))
                    .col(timestamp_with_time_zone("updated_at").default(Expr::current_timestamp()))
                    .foreign_key(
                        ForeignKey::create()
                            .name("sites_organization_fk")
                            .from("sites", "organization_id")
                            .to("organizations", "organization_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .index(
                        Index::create()
                            .name("sites_organization_name_key")
                            .col("organization_id")
                            .col("name")
                            .unique(),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("agents")
                    .col(text("agent_id").primary_key())
                    .col(text("organization_id"))
                    .col(text("site_id"))
                    .col(text_uniq("key_id"))
                    .col(text("jwk_thumbprint"))
                    .col(json_binary("public_jwk"))
                    .col(text_null("certificate_pem"))
                    .col(text_uniq("namespace"))
                    .col(text("protocol_version"))
                    .col(json_binary("controller_actions"))
                    .col(timestamp_with_time_zone("enrolled_at"))
                    .col(timestamp_with_time_zone("last_enrolled_at"))
                    .foreign_key(
                        ForeignKey::create()
                            .name("agents_organization_fk")
                            .from("agents", "organization_id")
                            .to("organizations", "organization_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("agents_site_fk")
                            .from("agents", "site_id")
                            .to("sites", "site_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("devices")
                    .col(text("device_id").primary_key())
                    .col(text("agent_id"))
                    .col(text("site_id"))
                    .col(text("kind"))
                    .col(text("display_name"))
                    .col(text("state"))
                    .col(text("transport"))
                    .col(text("hardware_fingerprint"))
                    .col(json_binary("capabilities"))
                    .col(timestamp_with_time_zone("first_seen_at"))
                    .col(timestamp_with_time_zone("last_seen_at"))
                    .check(Expr::cust("kind IN ('printer', 'scale', 'scanner')"))
                    .check(Expr::cust(
                        "state IN ('discovered', 'pending_approval', 'online', 'offline', 'degraded', 'blocked')",
                    ))
                    .foreign_key(
                        ForeignKey::create()
                            .name("devices_agent_fk")
                            .from("devices", "agent_id")
                            .to("agents", "agent_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("devices_site_fk")
                            .from("devices", "site_id")
                            .to("sites", "site_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .index(
                        Index::create()
                            .name("devices_agent_hardware_key")
                            .col("agent_id")
                            .col("hardware_fingerprint")
                            .unique(),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("devices_site_state")
                    .table("devices")
                    .col("site_id")
                    .col("state")
                    .to_owned(),
            )
            .await?;
        Ok(())
    }
}
