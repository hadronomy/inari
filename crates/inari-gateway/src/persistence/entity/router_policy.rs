use sea_orm::entity::prelude::*;

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "router_policies")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub organization_id: String,
    pub authority_revision: i64,
    pub policy_revision: Option<i64>,
    pub generation: i64,
    pub configuration_digest: Option<Vec<u8>>,
    pub policy_digest: Option<Vec<u8>>,
    #[sea_orm(column_type = "JsonBinary", nullable)]
    pub signed_policy: Option<Json>,
    pub expires_at: Option<DateTimeWithTimeZone>,
    pub acknowledged_at: Option<DateTimeWithTimeZone>,
}

impl ActiveModelBehavior for ActiveModel {}
