use chrono::{DateTime, Utc};
use inari_gateway::protocol::{
    AgentId, ControllerCommand, OrganizationId, ProtocolVersion, SiteId,
};
use jsonwebtoken::jwk::Jwk;

pub use inari_gateway::protocol::{
    AgentPublicationList, CommandHistory, JobList, JobReceipt, JobRequest,
};

#[derive(Debug, Clone)]
pub(super) struct StoredAgentEnrollment {
    pub(super) agent_id: AgentId,
    pub(super) organization_id: OrganizationId,
    pub(super) site_id: SiteId,
    pub(super) key_id: String,
    pub(super) public_jwk_fingerprint: String,
    pub(super) public_jwk: Jwk,
    pub(super) certificate_pem: Option<String>,
    pub(super) namespace: String,
    pub(super) protocol_version: ProtocolVersion,
    pub(super) controller_actions: Vec<String>,
    pub(super) enrolled_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub(super) struct StoredControllerCommand {
    pub(super) agent_id: inari_gateway::protocol::AgentId,
    pub(super) namespace: String,
    pub(super) command_id: inari_gateway::protocol::JobId,
    pub(super) command: ControllerCommand,
}
