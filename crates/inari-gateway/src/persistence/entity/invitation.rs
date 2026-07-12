use sea_orm::entity::prelude::*;

use super::value::{InvitationState, StoredSnapshot};

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "invitations")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub invitation_id: String,
    pub organization_id: String,
    pub site_id: String,
    #[sea_orm(column_type = "Text", nullable)]
    pub label: Option<String>,
    pub secret_digest: Vec<u8>,
    pub state: InvitationState,
    pub created_at: DateTimeWithTimeZone,
    pub expires_at: DateTimeWithTimeZone,
    pub claimed_at: Option<DateTimeWithTimeZone>,
    pub enrolled_at: Option<DateTimeWithTimeZone>,
    pub online_at: Option<DateTimeWithTimeZone>,
    pub revoked_at: Option<DateTimeWithTimeZone>,
    pub failed_at: Option<DateTimeWithTimeZone>,
    #[sea_orm(column_type = "Text", nullable)]
    pub last_error: Option<String>,
    #[sea_orm(column_type = "Text", nullable)]
    pub bound_agent_id: Option<String>,
    #[sea_orm(column_type = "Text", nullable)]
    pub bound_key_id: Option<String>,
    #[sea_orm(column_type = "JsonBinary", nullable)]
    pub latest_snapshot: Option<StoredSnapshot>,
}

impl ActiveModelBehavior for ActiveModel {}
