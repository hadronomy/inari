use inari_gateway::audit::{AuditEvent, AuditEventDraft};
use inari_gateway::protocol::{
    AgentDetail, AgentId, AgentStatus, AgentSummary, DeviceSummary, SiteId, SiteSummary,
};

use super::{AgentPublicationList, ManagedGatewayController};
use crate::error::{AppError, AppResult};

impl ManagedGatewayController {
    pub async fn sites(&self) -> AppResult<Vec<SiteSummary>> {
        self.ensure_enabled()?;
        self.inner
            .store
            .sites(&self.inner.organization.id)
            .await
    }

    pub async fn agents(&self, site_id: Option<&SiteId>) -> AppResult<Vec<AgentSummary>> {
        self.ensure_enabled()?;
        self.inner
            .store
            .agents(&self.inner.organization.id, site_id)
            .await
    }

    pub async fn agent(&self, agent_id: &AgentId) -> AppResult<AgentDetail> {
        let summary = self
            .agents(None)
            .await?
            .into_iter()
            .find(|agent| &agent.agent_id == agent_id)
            .ok_or_else(|| AppError::not_found("Agent was not found."))?;
        let latest_status = self
            .inner
            .store
            .latest_status(agent_id)
            .await?;
        Ok(AgentDetail { summary, latest_status })
    }

    pub async fn devices(&self, agent_id: &AgentId) -> AppResult<Vec<DeviceSummary>> {
        self.ensure_enabled()?;
        self.inner.store.devices(agent_id).await
    }

    pub async fn audit_events(
        &self,
        before: Option<i64>,
        limit: u16,
    ) -> AppResult<Vec<AuditEvent>> {
        self.ensure_enabled()?;
        self.inner
            .store
            .repository
            .audit_events(&self.inner.organization.id, before, limit)
            .await
            .map_err(Into::into)
    }

    pub async fn record_audit_event(&self, draft: AuditEventDraft) -> AppResult<()> {
        self.ensure_enabled()?;
        self.inner
            .store
            .repository
            .record_audit_event(&draft)
            .await
            .map_err(Into::into)
    }

    pub async fn list_publications(&self, agent_id: &str) -> AppResult<AgentPublicationList> {
        self.ensure_enabled()?;
        self.inner
            .store
            .list_publications(agent_id)
            .await
    }

    pub async fn agent_status(&self, agent_id: &AgentId) -> AppResult<AgentStatus> {
        self.ensure_enabled()?;
        self.inner
            .store
            .latest_status(agent_id)
            .await?
            .ok_or_else(|| AppError::not_found("No status has been observed for this agent."))
    }
}
