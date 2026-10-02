use sea_orm::entity::prelude::*;

use super::value::StoredManagedWorkPreflightRequest;

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "managed_work_preflights")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub preflight_id: String,
    #[sea_orm(column_type = "JsonBinary")]
    pub request: StoredManagedWorkPreflightRequest,
    pub dispatch_key_id: String,
    pub capability_digest: String,
    pub work_expires_at: DateTimeWithTimeZone,
    pub idempotency_expires_at: DateTimeWithTimeZone,
    pub submit_before: DateTimeWithTimeZone,
    pub created_at: DateTimeWithTimeZone,
}

impl ActiveModelBehavior for ActiveModel {}
