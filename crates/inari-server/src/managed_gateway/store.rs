use chrono::Utc;
use inari_gateway::protocol::{AgentId, AgentStatus, JobId, JobRecord, ManagedWorkId};
use inari_gateway::protocol::{AgentSummary, DeviceSummary, OrganizationId, SiteId, SiteSummary};
use inari_gateway::{
    GatewayRepository, ManagedWorkAdmission, ManagedWorkTargetRecord, NewManagedWorkPreflight,
    PersistedManagedWork, PersistedManagedWorkDispatch, RouterAdmission,
};
use sha2::{Digest, Sha256};

use super::models::{AgentPublicationList, JobList, JobRequest, StoredControllerCommand};
use crate::error::{AppError, AppResult};

pub(super) struct ManagedGatewayStore {
    repository: Option<GatewayRepository>,
}

impl ManagedGatewayStore {
    pub(super) fn new(repository: Option<GatewayRepository>) -> Self {
        Self { repository }
    }

    pub(super) const fn is_available(&self) -> bool {
        self.repository.is_some()
    }

    pub(super) fn repository(&self) -> AppResult<&GatewayRepository> {
        self.repository.as_ref().ok_or_else(|| {
            AppError::service_unavailable("Managed gateway persistence is not enabled.")
        })
    }

    pub(super) async fn sites(
        &self,
        organization_id: &OrganizationId,
    ) -> AppResult<Vec<SiteSummary>> {
        self.repository()?
            .sites(organization_id)
            .await
            .map_err(Into::into)
    }

    pub(super) async fn agents(
        &self,
        organization_id: &OrganizationId,
        site_id: Option<&SiteId>,
    ) -> AppResult<Vec<AgentSummary>> {
        self.repository()?
            .agents(organization_id, site_id)
            .await
            .map_err(Into::into)
    }

    pub(super) async fn devices(&self, agent_id: &AgentId) -> AppResult<Vec<DeviceSummary>> {
        self.repository()?
            .devices(agent_id)
            .await
            .map_err(Into::into)
    }

    pub(super) async fn enqueue_command(
        &self,
        router_admission: &RouterAdmission,
        agent_id: &AgentId,
        job_id: &inari_gateway::protocol::JobId,
        request: JobRequest,
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
        let request_fingerprint: [u8; 32] = Sha256::digest(serde_json::to_vec(&request)?).into();
        let command = request.command;
        let persisted = self
            .repository()?
            .enqueue_command(
                router_admission,
                agent_id.as_str(),
                Some(job_id.as_str()),
                &request_fingerprint,
                move |sequence, command_id, message_id, issued_at| {
                    let command = command.into_message(
                        message_id.into(),
                        command_id.into(),
                        sequence,
                        issued_at,
                    );
                    Ok(command)
                },
            )
            .await?;
        Ok(StoredControllerCommand {
            agent_id: persisted.agent_id,
            namespace: persisted.namespace,
            command_id: persisted.command_id,
            command: persisted.command,
        })
    }

    pub(super) async fn mark_command_published(
        &self,
        agent_id: &str,
        command_id: &str,
    ) -> AppResult<()> {
        self.repository()?
            .mark_command_published(agent_id, command_id, Utc::now())
            .await?;
        Ok(())
    }

    pub(super) async fn command_history(
        &self,
        agent_id: &str,
        from_sequence: u64,
    ) -> AppResult<(inari_gateway::protocol::ProtocolVersion, Vec<StoredControllerCommand>)> {
        let (selected_protocol_version, commands) = self
            .repository()?
            .command_history(agent_id, from_sequence)
            .await?;
        Ok((
            selected_protocol_version,
            commands
                .into_iter()
                .map(|command| StoredControllerCommand {
                    agent_id: command.agent_id,
                    namespace: command.namespace,
                    command_id: command.command_id,
                    command: command.command,
                })
                .collect(),
        ))
    }

    pub(super) async fn job(&self, job_id: &JobId) -> AppResult<JobRecord> {
        self.repository()?
            .job(job_id)
            .await
            .map(job_record)
            .map_err(Into::into)
    }

    pub(super) async fn jobs(&self, agent_id: &AgentId) -> AppResult<JobList> {
        let jobs = self
            .repository()?
            .jobs(agent_id.as_str())
            .await?
            .into_iter()
            .map(job_record)
            .collect();
        Ok(JobList { jobs })
    }

    pub(super) async fn record_publication(
        &self,
        agent_id: String,
        key: String,
        message: inari_gateway::protocol::AgentPublication,
    ) -> AppResult<()> {
        self.repository()?
            .record_publication(&agent_id, &key, &message, Utc::now())
            .await?;
        Ok(())
    }

    pub(super) async fn list_publications(
        &self,
        agent_id: &str,
    ) -> AppResult<AgentPublicationList> {
        let publications = self
            .repository()?
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
        self.repository()?
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

    pub(super) async fn admit_managed_work<F>(
        &self,
        admission: ManagedWorkAdmission<'_>,
        build_command: F,
    ) -> AppResult<(PersistedManagedWork, Option<StoredControllerCommand>)>
    where
        F: FnOnce(
            inari_gateway::ManagedDispatchAllocation<'_>,
        ) -> inari_gateway::GatewayResult<inari_gateway::NewManagedPayload>,
    {
        let PersistedManagedWorkDispatch { managed_work, command } = self
            .repository()?
            .admit_managed_work(admission, build_command)
            .await?;
        let command = command.map(|command| StoredControllerCommand {
            agent_id: command.agent_id,
            namespace: command.namespace,
            command_id: command.command_id,
            command: command.command,
        });
        Ok((managed_work, command))
    }

    pub(super) async fn replay_managed_work(
        &self,
        managed_work_id: &ManagedWorkId,
        organization_id: &OrganizationId,
        idempotency_key: &str,
        print_intent_id: &str,
        request_fingerprint: &[u8; 32],
    ) -> AppResult<Option<(PersistedManagedWork, Option<StoredControllerCommand>)>> {
        Ok(self
            .repository()?
            .replay_managed_work(
                managed_work_id,
                organization_id.as_str(),
                idempotency_key,
                print_intent_id,
                request_fingerprint,
            )
            .await?
            .map(|PersistedManagedWorkDispatch { managed_work, command }| {
                let command = command.map(|command| StoredControllerCommand {
                    agent_id: command.agent_id,
                    namespace: command.namespace,
                    command_id: command.command_id,
                    command: command.command,
                });
                (managed_work, command)
            }))
    }

    pub(super) async fn managed_work_target(
        &self,
        organization_id: &OrganizationId,
        site_id: &SiteId,
        agent_id: &AgentId,
        device_id: &inari_gateway::protocol::DeviceId,
    ) -> AppResult<ManagedWorkTargetRecord> {
        self.repository()?
            .managed_work_target(
                organization_id.as_str(),
                site_id.as_str(),
                agent_id.as_str(),
                device_id.as_str(),
            )
            .await
            .map_err(Into::into)
    }

    pub(super) async fn create_managed_work_preflight(
        &self,
        preflight: NewManagedWorkPreflight,
    ) -> AppResult<()> {
        self.repository()?
            .create_managed_work_preflight(preflight)
            .await
            .map_err(Into::into)
    }

    pub(super) async fn managed_work(
        &self,
        managed_work_id: &ManagedWorkId,
    ) -> AppResult<PersistedManagedWork> {
        self.repository()?
            .managed_work(managed_work_id)
            .await
            .map_err(Into::into)
    }
}

fn job_record(job: inari_gateway::PersistedCommand) -> JobRecord {
    JobRecord {
        job_id: job.command_id,
        agent_id: job.agent_id,
        state: job.state,
        issued_at: job.issued_at,
        updated_at: job.updated_at,
    }
}
