use sea_orm::entity::prelude::*;

use super::value::{DeviceKind, DeviceState, DeviceTransport, StoredCapabilities};

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "devices")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub device_id: String,
    pub agent_id: String,
    pub site_id: String,
    pub kind: DeviceKind,
    pub display_name: String,
    pub state: DeviceState,
    pub transport: DeviceTransport,
    pub hardware_fingerprint: String,
    #[sea_orm(column_type = "JsonBinary")]
    pub capabilities: StoredCapabilities,
    pub first_seen_at: DateTimeWithTimeZone,
    pub last_seen_at: DateTimeWithTimeZone,
    #[sea_orm(belongs_to, from = "site_id", to = "site_id")]
    pub site: HasOne<super::site::Entity>,
}

impl ActiveModelBehavior for ActiveModel {}
