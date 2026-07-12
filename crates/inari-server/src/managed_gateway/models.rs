use chrono::{DateTime, Utc};
use inari_gateway::protocol::{ControllerCommand, ProtocolVersion};
use serde_json::Value;

pub use inari_gateway::protocol::{
    AgentPublicationList, CommandHistoryResponse, ControllerCommandState,
    SubmitControllerCommandRequest, SubmitControllerCommandResponse,
};

pub(super) enum EnrollmentCredential {
    ConfiguredToken { token_hash: String },
    Invite { invite_id: String },
}

#[derive(Debug, Clone)]
pub(super) struct StoredAgentEnrollment {
    pub(super) agent_id: String,
    pub(super) key_id: String,
    pub(super) public_jwk_fingerprint: String,
    pub(super) public_jwk: Value,
    pub(super) certificate_pem: Option<String>,
    pub(super) namespace: String,
    pub(super) protocol_version: ProtocolVersion,
    pub(super) controller_actions: Vec<String>,
    pub(super) enrolled_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub(super) struct StoredControllerCommand {
    pub(super) agent_id: String,
    pub(super) namespace: String,
    pub(super) command_id: String,
    pub(super) command: ControllerCommand,
}
