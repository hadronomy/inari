use sea_orm::entity::prelude::*;

use super::value::{AgentKeyPurpose, StoredJwk};

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "agent_verification_keys")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub key_id: String,
    pub agent_id: String,
    pub purpose: AgentKeyPurpose,
    #[sea_orm(unique)]
    pub jwk_thumbprint: String,
    #[sea_orm(column_type = "JsonBinary")]
    pub public_jwk: StoredJwk,
    pub registered_at: DateTimeWithTimeZone,
    pub retired_at: Option<DateTimeWithTimeZone>,
}

impl ActiveModelBehavior for ActiveModel {}
