use sea_orm::entity::prelude::*;

use super::value::StoredAuditDetail;

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "audit_events")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub event_id: i64,
    pub organization_id: String,
    pub actor_id: String,
    pub action: String,
    pub resource_kind: String,
    #[sea_orm(column_type = "Text", nullable)]
    pub resource_id: Option<String>,
    pub outcome: String,
    #[sea_orm(column_type = "Text", nullable)]
    pub request_id: Option<String>,
    #[sea_orm(column_type = "JsonBinary")]
    pub detail: StoredAuditDetail,
    pub occurred_at: DateTimeWithTimeZone,
}

impl ActiveModelBehavior for ActiveModel {}
