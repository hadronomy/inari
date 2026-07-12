use chrono::Utc;
use inari_gateway::protocol::{AgentId, AgentStatus};
use inari_gateway::{
    AgentEnrollmentRecord, EnrollmentCredential as PersistedEnrollmentCredential, GatewayError,
    GatewayRepository,
};
use serde_json::Value;

use super::models::{
    AgentPublicationList, CommandHistoryResponse, EnrollmentCredential, StoredAgentEnrollment,
    StoredControllerCommand, SubmitControllerCommandRequest,
};
use crate::error::{AppError, AppResult};

pub(super) struct ManagedGatewayStore {
    pub(super) repository: GatewayRepository,
}

impl ManagedGatewayStore {
    pub(super) fn new(repository: GatewayRepository) -> Self {
        Self { repository }
    }

    pub(super) async fn enroll(
        &self,
        enrollment: StoredAgentEnrollment,
        credential: EnrollmentCredential,
        snapshot: Value,
    ) -> AppResult<()> {
        let credential = match credential {
            EnrollmentCredential::ConfiguredToken { token_hash } => {
                let mut digest = [0_u8; 32];
                hex::decode_to_slice(token_hash, &mut digest).map_err(|source| {
                    AppError::internal(
                        "enrollment_token_hash_invalid",
                        "Configured enrollment token hash is invalid.",
                    )
                    .with_source(source)
                })?;
                PersistedEnrollmentCredential::ConfiguredToken { digest }
            },
            EnrollmentCredential::Invite { invite_id } => {
                PersistedEnrollmentCredential::Invitation { invitation_id: invite_id }
            },
        };
        self.repository
            .enroll_agent(
                AgentEnrollmentRecord {
                    agent_id: enrollment.agent_id,
                    key_id: enrollment.key_id,
                    jwk_thumbprint: enrollment.public_jwk_fingerprint,
                    public_jwk: enrollment.public_jwk,
                    certificate_pem: enrollment.certificate_pem,
                    namespace: enrollment.namespace,
                    protocol_version: enrollment.protocol_version,
                    controller_actions: enrollment.controller_actions,
                    enrolled_at: enrollment.enrolled_at,
                },
                credential,
                &snapshot,
            )
            .await
            .map_err(AppError::from)
    }

    pub(super) async fn enqueue_command(
        &self,
        request: SubmitControllerCommandRequest,
        controller_actions: &[String],
    ) -> AppResult<StoredControllerCommand> {
        if !controller_actions
            .iter()
            .any(|action| action == request.command.required_action())
        {
            return Err(AppError::forbidden(
                "Controller command is not allowed by managed gateway permissions.",
            ));
        }
        let agent_id = request.agent_id;
        let command_id = request.command_id;
        let command = request.command;
        let persisted = self
            .repository
            .enqueue_command(
                &agent_id,
                command_id.as_deref(),
                move |sequence, command_id, message_id, issued_at| {
                    let command = command.into_message(
                        message_id.into(),
                        command_id.into(),
                        sequence,
                        issued_at,
                    );
                    serde_json::to_value(command).map_err(GatewayError::from)
                },
            )
            .await?;
        Ok(StoredControllerCommand {
            agent_id: persisted.agent_id,
            namespace: persisted.namespace,
            command_id: persisted.command_id,
            command: serde_json::from_value(persisted.command)?,
        })
    }

    pub(super) async fn mark_command_published(
        &self,
        agent_id: &str,
        command_id: &str,
    ) -> AppResult<()> {
        self.repository
            .mark_command_published(agent_id, command_id, Utc::now())
            .await?;
        Ok(())
    }

    pub(super) async fn command_history(
        &self,
        agent_id: &str,
        from_sequence: u64,
    ) -> AppResult<CommandHistoryResponse> {
        let (selected_protocol_version, commands) = self
            .repository
            .command_history(agent_id, from_sequence)
            .await?;
        let commands = commands
            .into_iter()
            .map(serde_json::from_value)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(CommandHistoryResponse { selected_protocol_version, commands })
    }

    pub(super) async fn record_publication(
        &self,
        agent_id: String,
        key: String,
        message: inari_gateway::protocol::AgentPublication,
    ) -> AppResult<()> {
        self.repository
            .record_publication(&agent_id, &key, &message, Utc::now())
            .await?;
        Ok(())
    }

    pub(super) async fn list_publications(
        &self,
        agent_id: &str,
    ) -> AppResult<AgentPublicationList> {
        let publications = self
            .repository
            .publications(agent_id)
            .await?
            .into_iter()
            .map(|publication| inari_gateway::protocol::StoredAgentPublication {
                key: publication.key,
                received_at: publication.received_at,
                message: publication.message,
            })
            .collect();
        Ok(AgentPublicationList { publications })
    }

    pub(super) async fn latest_status(&self, agent_id: &AgentId) -> AppResult<Option<AgentStatus>> {
        self.repository
            .latest_status(agent_id.as_str())
            .await
            .map(|status| {
                status.map(|status| AgentStatus {
                    agent_id: agent_id.clone(),
                    message_id: status.message_id,
                    received_at: status.received_at,
                    snapshot: status.snapshot,
                })
            })
            .map_err(AppError::from)
    }
}
