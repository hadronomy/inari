use jsonwebtoken::jwk::Jwk;
use sea_orm::FromJsonQueryResult;
use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

use crate::protocol::{
    AgentPublication, ControllerCommand, DeviceCapability, GatewaySnapshot, StructuredFields,
};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, FromJsonQueryResult)]
pub struct StoredJwk(pub Jwk);

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, FromJsonQueryResult)]
pub struct StoredActions(pub Vec<String>);

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, FromJsonQueryResult)]
pub struct StoredSnapshot(pub GatewaySnapshot);

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, FromJsonQueryResult)]
pub struct StoredCommand(pub ControllerCommand);

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, FromJsonQueryResult)]
pub struct StoredPublication(pub AgentPublication);

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, FromJsonQueryResult)]
pub struct StoredCapabilities(pub Vec<DeviceCapability>);

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, FromJsonQueryResult)]
pub struct StoredAuditDetail(pub StructuredFields);

#[derive(Clone, Debug, PartialEq, Eq, EnumIter, DeriveActiveEnum)]
#[sea_orm(rs_type = "String", db_type = "Text")]
pub enum InvitationState {
    #[sea_orm(string_value = "created")]
    Created,
    #[sea_orm(string_value = "claimed")]
    Claimed,
    #[sea_orm(string_value = "enrolled")]
    Enrolled,
    #[sea_orm(string_value = "online")]
    Online,
    #[sea_orm(string_value = "expired")]
    Expired,
    #[sea_orm(string_value = "failed")]
    Failed,
    #[sea_orm(string_value = "revoked")]
    Revoked,
}

#[derive(Clone, Debug, PartialEq, Eq, EnumIter, DeriveActiveEnum)]
#[sea_orm(rs_type = "String", db_type = "Text")]
pub enum CommandState {
    #[sea_orm(string_value = "queued")]
    Queued,
    #[sea_orm(string_value = "published")]
    Published,
    #[sea_orm(string_value = "accepted")]
    Accepted,
    #[sea_orm(string_value = "rejected")]
    Rejected,
    #[sea_orm(string_value = "completed")]
    Completed,
    #[sea_orm(string_value = "failed")]
    Failed,
    #[sea_orm(string_value = "superseded")]
    Superseded,
}

#[derive(Clone, Debug, PartialEq, Eq, EnumIter, DeriveActiveEnum)]
#[sea_orm(rs_type = "String", db_type = "Text")]
pub enum DeviceKind {
    #[sea_orm(string_value = "printer")]
    Printer,
    #[sea_orm(string_value = "scale")]
    Scale,
    #[sea_orm(string_value = "scanner")]
    Scanner,
}

#[derive(Clone, Debug, PartialEq, Eq, EnumIter, DeriveActiveEnum)]
#[sea_orm(rs_type = "String", db_type = "Text")]
pub enum DeviceState {
    #[sea_orm(string_value = "discovered")]
    Discovered,
    #[sea_orm(string_value = "pending_approval")]
    PendingApproval,
    #[sea_orm(string_value = "online")]
    Online,
    #[sea_orm(string_value = "offline")]
    Offline,
    #[sea_orm(string_value = "degraded")]
    Degraded,
    #[sea_orm(string_value = "blocked")]
    Blocked,
}

#[derive(Clone, Debug, PartialEq, Eq, EnumIter, DeriveActiveEnum)]
#[sea_orm(rs_type = "String", db_type = "Text")]
pub enum DeviceTransport {
    #[sea_orm(string_value = "spooler")]
    Spooler,
    #[sea_orm(string_value = "network")]
    Network,
    #[sea_orm(string_value = "usb")]
    Usb,
    #[sea_orm(string_value = "hid")]
    Hid,
    #[sea_orm(string_value = "serial")]
    Serial,
}

#[derive(Clone, Debug, PartialEq, Eq, EnumIter, DeriveActiveEnum)]
#[sea_orm(rs_type = "String", db_type = "Text")]
pub enum PublicationType {
    #[sea_orm(string_value = "agent.command.accepted")]
    CommandAccepted,
    #[sea_orm(string_value = "agent.command.rejected")]
    CommandRejected,
    #[sea_orm(string_value = "agent.runtime.event")]
    RuntimeEvent,
    #[sea_orm(string_value = "agent.status.snapshot")]
    StatusSnapshot,
    #[sea_orm(string_value = "agent.error")]
    Error,
}

impl PublicationType {
    pub fn from_message(message: &AgentPublication) -> Self {
        match message {
            AgentPublication::CommandAccepted { .. } => Self::CommandAccepted,
            AgentPublication::CommandRejected { .. } => Self::CommandRejected,
            AgentPublication::RuntimeEvent { .. } => Self::RuntimeEvent,
            AgentPublication::StatusSnapshot { .. } => Self::StatusSnapshot,
            AgentPublication::Error { .. } => Self::Error,
        }
    }
}
