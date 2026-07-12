use sea_orm::entity::prelude::*;

use super::value::{CommandState, StoredCommand};

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "commands")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub command_id: String,
    pub agent_id: String,
    #[sea_orm(unique)]
    pub message_id: String,
    pub sequence: i64,
    pub state: CommandState,
    #[sea_orm(column_type = "JsonBinary")]
    pub command: StoredCommand,
    pub request_fingerprint: Vec<u8>,
    pub issued_at: DateTimeWithTimeZone,
    pub published_at: Option<DateTimeWithTimeZone>,
    pub updated_at: DateTimeWithTimeZone,
}

impl ActiveModelBehavior for ActiveModel {}
