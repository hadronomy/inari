use sea_orm::entity::prelude::*;

use super::value::{StoredActions, StoredJwk};

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "agents")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub agent_id: String,
    pub organization_id: String,
    pub site_id: String,
    #[sea_orm(unique)]
    pub key_id: String,
    pub jwk_thumbprint: String,
    #[sea_orm(column_type = "JsonBinary")]
    pub public_jwk: StoredJwk,
    #[sea_orm(column_type = "Text", nullable)]
    pub certificate_pem: Option<String>,
    #[sea_orm(unique)]
    pub namespace: String,
    pub protocol_version: String,
    #[sea_orm(column_type = "JsonBinary")]
    pub controller_actions: StoredActions,
    pub enrolled_at: DateTimeWithTimeZone,
    pub last_enrolled_at: DateTimeWithTimeZone,
    #[sea_orm(belongs_to, from = "site_id", to = "site_id")]
    pub site: HasOne<super::site::Entity>,
    #[sea_orm(has_many)]
    pub publications: HasMany<super::publication::Entity>,
}

impl ActiveModelBehavior for ActiveModel {}
