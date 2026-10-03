pub use inari_gateway::protocol::{
    AgentPublicationList, CommandHistory, JobList, JobReceipt, JobRequest,
};

#[derive(Debug, Clone)]
pub(super) struct StoredControllerCommand {
    pub(super) agent_id: inari_gateway::protocol::AgentId,
    pub(super) namespace: String,
    pub(super) command_id: inari_gateway::protocol::JobId,
    pub(super) command: inari_gateway::CommandContent,
}
