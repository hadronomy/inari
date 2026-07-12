use sea_orm_migration::{prelude::*, schema::*};

pub struct Migration;

impl MigrationName for Migration {
    fn name(&self) -> &str {
        "m20260712_223024_create_enrollment"
    }
}

#[async_trait::async_trait]
impl MigrationTrait for Migration {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table("invitations")
                    .col(text("invitation_id").primary_key())
                    .col(text("organization_id"))
                    .col(text("site_id"))
                    .col(text_null("label"))
                    .col(binary("secret_digest"))
                    .col(text("state"))
                    .col(timestamp_with_time_zone("created_at"))
                    .col(timestamp_with_time_zone("expires_at"))
                    .col(timestamp_with_time_zone_null("claimed_at"))
                    .col(timestamp_with_time_zone_null("enrolled_at"))
                    .col(timestamp_with_time_zone_null("online_at"))
                    .col(timestamp_with_time_zone_null("revoked_at"))
                    .col(timestamp_with_time_zone_null("failed_at"))
                    .col(text_null("last_error"))
                    .col(text_null("bound_agent_id"))
                    .col(text_null("bound_key_id"))
                    .col(json_binary_null("latest_snapshot"))
                    .check(Expr::cust("octet_length(secret_digest) = 32"))
                    .check(Expr::cust(
                        "state IN ('created', 'claimed', 'enrolled', 'online', 'expired', 'failed', 'revoked')",
                    ))
                    .foreign_key(
                        ForeignKey::create()
                            .name("invitations_organization_fk")
                            .from("invitations", "organization_id")
                            .to("organizations", "organization_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("invitations_site_fk")
                            .from("invitations", "site_id")
                            .to("sites", "site_id")
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("invitations_state_expires_at")
                    .table("invitations")
                    .col("state")
                    .col("expires_at")
                    .to_owned(),
            )
            .await?;
        manager
            .create_table(
                Table::create()
                    .table("invitation_attempts")
                    .col(text("invitation_id"))
                    .col(timestamp_with_time_zone("attempted_at"))
                    .primary_key(
                        Index::create()
                            .name("invitation_attempts_pkey")
                            .col("invitation_id")
                            .col("attempted_at"),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("invitation_attempts_invitation_fk")
                            .from("invitation_attempts", "invitation_id")
                            .to("invitations", "invitation_id")
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("invitation_attempts_window")
                    .table("invitation_attempts")
                    .col("invitation_id")
                    .col("attempted_at")
                    .to_owned(),
            )
            .await?;
        Ok(())
    }
}
