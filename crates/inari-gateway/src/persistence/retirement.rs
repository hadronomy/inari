use chrono::Utc;
use sea_orm::sea_query::Expr;
use sea_orm::{ColumnTrait, EntityTrait, QueryFilter, QueryOrder, QuerySelect, TransactionTrait};

use super::entity::value::InvitationState;
use super::entity::{agent, agent_verification_key, invitation};
use super::{GatewayRepository, stored_time};
use crate::audit::{AuditAction, AuditContext, AuditEventDraft, AuditOutcome, AuditResource};
use crate::protocol::{AgentId, OrganizationId};
use crate::{GatewayError, GatewayResult};

impl GatewayRepository {
    /// Permanently retires both Agent key purposes and closes every bound invitation.
    /// Repeated retirement writes no new audit event. Historical observations remain verifiable.
    pub async fn retire_agent_credentials(
        &self,
        organization_id: &OrganizationId,
        agent_id: &AgentId,
        audit: &AuditContext,
    ) -> GatewayResult<()> {
        let transaction = self.database.begin().await?;
        // Enrollment takes invitation locks before the Agent lock.
        invitation::Entity::find()
            .filter(invitation::Column::BoundAgentId.eq(agent_id.as_str()))
            .order_by_asc(invitation::Column::InvitationId)
            .lock_exclusive()
            .all(&transaction)
            .await?;
        let agent = agent::Entity::find_by_id(agent_id.as_str())
            .lock_exclusive()
            .one(&transaction)
            .await?
            .ok_or_else(|| GatewayError::NotFound("Agent was not found".into()))?;
        if agent.organization_id != organization_id.as_str() {
            return Err(GatewayError::NotFound("Agent was not found".into()));
        }
        let now = stored_time(Utc::now());
        let retired = agent_verification_key::Entity::update_many()
            .col_expr(agent_verification_key::Column::RetiredAt, Expr::value(now))
            .filter(agent_verification_key::Column::AgentId.eq(agent_id.as_str()))
            .filter(agent_verification_key::Column::RetiredAt.is_null())
            .exec(&transaction)
            .await?;
        invitation::Entity::update_many()
            .col_expr(invitation::Column::State, Expr::value(InvitationState::Revoked))
            .col_expr(invitation::Column::RevokedAt, Expr::value(now))
            .filter(invitation::Column::BoundAgentId.eq(agent_id.as_str()))
            .filter(
                invitation::Column::State
                    .is_in([InvitationState::Enrolled, InvitationState::Online]),
            )
            .exec(&transaction)
            .await?;
        if retired.rows_affected > 0 {
            super::audit::insert_audit_event(
                &transaction,
                &AuditEventDraft {
                    organization_id: organization_id.clone(),
                    actor_id: audit.actor_id.clone(),
                    action: AuditAction::AgentCredentialsRetired,
                    resource: AuditResource::Agent { agent_id: agent_id.clone() },
                    outcome: AuditOutcome::Succeeded,
                    request_id: audit.request_id.clone(),
                },
            )
            .await?;
        }
        transaction.commit().await?;
        Ok(())
    }
}
