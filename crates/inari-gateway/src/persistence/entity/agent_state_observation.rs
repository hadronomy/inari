use sea_orm::entity::prelude::*;

use super::value::StoredAgentStateObservation;

#[sea_orm::model]
#[derive(Clone, Debug, DeriveEntityModel)]
#[sea_orm(table_name = "agent_state_observations")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub envelope_id: String,
    pub agent_id: String,
    pub managed_work_id: String,
    #[sea_orm(column_type = "JsonBinary")]
    pub observation: StoredAgentStateObservation,
    pub received_at: DateTimeWithTimeZone,
}

impl ActiveModelBehavior for ActiveModel {}
