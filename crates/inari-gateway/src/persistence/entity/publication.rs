use sea_orm::entity::prelude::*;

use super::value::{PublicationType, StoredPublication};

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "publications")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub message_id: String,
    pub agent_id: String,
    pub key_expr: String,
    pub message_type: Option<PublicationType>,
    #[sea_orm(column_type = "JsonBinary")]
    pub payload: StoredPublication,
    pub received_at: DateTimeWithTimeZone,
    #[sea_orm(belongs_to, from = "agent_id", to = "agent_id")]
    pub agent: HasOne<super::agent::Entity>,
}

impl ActiveModelBehavior for ActiveModel {}
