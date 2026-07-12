use std::str::FromStr;

use bytes::Bytes;
use inari_gateway::protocol::{
    AgentId, JobId, JobKind, JobList, JobReceipt, JobRecord, JobRequest, JobState,
};
use sha2::{Digest, Sha256};
use zenoh::bytes::Encoding;

use super::{ManagedGatewayController, StoredControllerCommand};
use crate::error::{AppError, AppResult};
use crate::zenoh::KeyExpression;

impl ManagedGatewayController {
    pub async fn enqueue_job(
        &self,
        agent_id: &AgentId,
        job_id: JobId,
        request: JobRequest,
    ) -> AppResult<JobReceipt> {
        self.ensure_enabled()?;
        let command = self
            .inner
            .store
            .enqueue_command(agent_id, &job_id, request, &self.inner.config.controller_actions)
            .await?;

        let publish_result = self
            .publish_live_command(&command)
            .await;
        if publish_result.is_ok() {
            self.inner
                .store
                .mark_command_published(&command.agent_id, &command.command_id)
                .await?;
        } else if let Err(error) = &publish_result {
            tracing::debug!(
                error = %error,
                command_id = %command.command_id,
                agent_id = %command.agent_id,
                "queued managed gateway command could not be published live"
            );
        }

        Ok(JobReceipt {
            job_id,
            state: if publish_result.is_ok() { JobState::Published } else { JobState::Queued },
        })
    }

    pub async fn list_jobs(&self, agent_id: &AgentId) -> AppResult<JobList> {
        self.ensure_enabled()?;
        self.inner.store.jobs(agent_id).await
    }

    pub async fn job(&self, job_id: &JobId) -> AppResult<JobRecord> {
        self.ensure_enabled()?;
        self.inner.store.job(job_id).await
    }

    pub async fn cancel_job(&self, job_id: &JobId) -> AppResult<JobReceipt> {
        let job = self.job(job_id).await?;
        if matches!(
            job.state,
            JobState::Completed | JobState::Failed | JobState::Rejected | JobState::Superseded
        ) {
            return Err(AppError::conflict("A terminal job cannot be cancelled."));
        }
        let digest = Sha256::digest(job_id.as_str());
        let cancellation_id = format!("job_cancel_{}", &hex::encode(digest)[..32])
            .parse()
            .map_err(AppError::from)?;
        self.enqueue_job(
            &job.agent_id,
            cancellation_id,
            JobRequest { command: JobKind::CancelJob { job_id: job_id.to_string() } },
        )
        .await
    }

    async fn publish_live_command(&self, command: &StoredControllerCommand) -> AppResult<()> {
        let namespace =
            KeyExpression::from_str(command.namespace.trim_end_matches('/')).map_err(|source| {
                AppError::bad_request(format!("Invalid Zenoh command key: {source}"))
            })?;
        let key = namespace
            .join("commands")
            .and_then(|key| key.join("live"))
            .and_then(|key| key.join(&command.command_id))
            .map_err(|source| {
                AppError::bad_request(format!("Invalid Zenoh command key: {source}"))
            })?;
        let payload = serde_json::to_vec(&command.command).map_err(|source| {
            AppError::internal(
                "managed_gateway_command_serialization",
                "Failed to serialize controller command.",
            )
            .with_source(source)
        })?;
        self.inner
            .zenoh
            .put_bytes(key, Bytes::from(payload), Encoding::APPLICATION_JSON, None)
            .await
    }
}
