use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::{GatewaySnapshot, StructuredFields};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentPublicationList {
    pub publications: Vec<StoredAgentPublication>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredAgentPublication {
    pub key: String,
    pub received_at: DateTime<Utc>,
    pub message: AgentPublication,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum AgentPublication {
    #[serde(rename = "agent.command.accepted")]
    CommandAccepted {
        message_id: String,
        command_id: String,
        accepted_at: DateTime<Utc>,
        #[serde(default)]
        job: Option<StructuredFields>,
        detail: String,
    },
    #[serde(rename = "agent.command.rejected")]
    CommandRejected {
        message_id: String,
        command_id: String,
        rejected_at: DateTime<Utc>,
        code: String,
        detail: String,
    },
    #[serde(rename = "agent.runtime.event")]
    RuntimeEvent {
        message_id: String,
        occurred_at: DateTime<Utc>,
        event: RuntimeEvent,
        #[serde(default)]
        command_id: Option<String>,
        #[serde(default)]
        job_id: Option<String>,
    },
    #[serde(rename = "agent.status.snapshot")]
    StatusSnapshot { message_id: String, snapshot: Box<GatewaySnapshot> },
    #[serde(rename = "agent.error")]
    Error {
        message_id: String,
        occurred_at: DateTime<Utc>,
        code: String,
        detail: String,
        #[serde(default)]
        command_id: Option<String>,
        #[serde(default)]
        retriable: bool,
    },
}

impl AgentPublication {
    #[must_use]
    pub fn message_id(&self) -> &str {
        match self {
            Self::CommandAccepted { message_id, .. }
            | Self::CommandRejected { message_id, .. }
            | Self::RuntimeEvent { message_id, .. }
            | Self::StatusSnapshot { message_id, .. }
            | Self::Error { message_id, .. } => message_id,
        }
    }

    #[must_use]
    pub const fn message_type(&self) -> &'static str {
        match self {
            Self::CommandAccepted { .. } => "agent.command.accepted",
            Self::CommandRejected { .. } => "agent.command.rejected",
            Self::RuntimeEvent { .. } => "agent.runtime.event",
            Self::StatusSnapshot { .. } => "agent.status.snapshot",
            Self::Error { .. } => "agent.error",
        }
    }

    #[must_use]
    pub fn command_id(&self) -> Option<&str> {
        match self {
            Self::CommandAccepted { command_id, .. } | Self::CommandRejected { command_id, .. } => {
                Some(command_id)
            },
            Self::RuntimeEvent { command_id, .. } | Self::Error { command_id, .. } => {
                command_id.as_deref()
            },
            Self::StatusSnapshot { .. } => None,
        }
    }

    #[must_use]
    pub fn snapshot(&self) -> Option<&GatewaySnapshot> {
        match self {
            Self::StatusSnapshot { snapshot, .. } => Some(snapshot.as_ref()),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RuntimeEvent {
    pub sequence: u64,
    pub resource_kind: String,
    pub resource_id: String,
    pub event_type: String,
    pub occurred_at: DateTime<Utc>,
    #[serde(default)]
    pub payload: StructuredFields,
}
