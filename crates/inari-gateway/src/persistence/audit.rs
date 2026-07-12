use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ConnectionTrait, EntityTrait, QueryFilter, QueryOrder,
    QuerySelect,
};

use super::entity::audit_event;
use super::{GatewayRepository, utc_time};
use crate::audit::{AuditAction, AuditEvent, AuditEventDraft, AuditOutcome, AuditResource};
use crate::identity::ActorId;
use crate::protocol::OrganizationId;
use crate::{GatewayError, GatewayResult};

pub(super) async fn insert_audit_event<C>(
    connection: &C,
    draft: &AuditEventDraft,
) -> GatewayResult<()>
where
    C: ConnectionTrait,
{
    let (resource_kind, resource_id) = draft.resource.storage_parts();
    audit_event::ActiveModel {
        organization_id: Set(draft
            .organization_id
            .as_str()
            .to_owned()),
        actor_id: Set(draft.actor_id.as_str().to_owned()),
        action: Set(draft.action.as_str().to_owned()),
        resource_kind: Set(resource_kind.to_owned()),
        resource_id: Set(resource_id.map(str::to_owned)),
        outcome: Set(draft.outcome.as_str().to_owned()),
        request_id: Set(draft.request_id.clone()),
        ..Default::default()
    }
    .insert(connection)
    .await?;
    Ok(())
}

impl GatewayRepository {
    pub async fn record_audit_event(&self, draft: &AuditEventDraft) -> GatewayResult<()> {
        insert_audit_event(&self.database, draft).await
    }

    pub async fn audit_events(
        &self,
        organization_id: &OrganizationId,
        before: Option<i64>,
        limit: u16,
    ) -> GatewayResult<Vec<AuditEvent>> {
        let mut query = audit_event::Entity::find().filter(
            audit_event::COLUMN
                .organization_id
                .eq(organization_id.as_str()),
        );
        if let Some(before) = before {
            query = query.filter(audit_event::COLUMN.event_id.lt(before));
        }
        query
            .order_by_desc(audit_event::COLUMN.event_id)
            .limit(u64::from(limit))
            .all(&self.database)
            .await?
            .into_iter()
            .map(audit_from_model)
            .collect()
    }
}

fn audit_from_model(model: audit_event::Model) -> GatewayResult<AuditEvent> {
    Ok(AuditEvent {
        event_id: model.event_id,
        actor_id: ActorId::try_from(model.actor_id)?,
        action: parse_action(&model.action)?,
        resource: AuditResource::from_storage(&model.resource_kind, model.resource_id)?,
        outcome: parse_outcome(&model.outcome)?,
        request_id: model.request_id,
        occurred_at: utc_time(model.occurred_at),
    })
}

fn parse_action(value: &str) -> GatewayResult<AuditAction> {
    match value {
        "job.created" => Ok(AuditAction::JobCreated),
        "job.cancellation_requested" => Ok(AuditAction::JobCancellationRequested),
        "invitation.created" => Ok(AuditAction::InvitationCreated),
        "invitation.revoked" => Ok(AuditAction::InvitationRevoked),
        "agent.enrolled" => Ok(AuditAction::AgentEnrolled),
        "zenoh.read" => Ok(AuditAction::ZenohRead),
        "zenoh.write" => Ok(AuditAction::ZenohWrite),
        other => Err(GatewayError::CorruptState(format!("unknown audit action {other:?}"))),
    }
}

fn parse_outcome(value: &str) -> GatewayResult<AuditOutcome> {
    match value {
        "succeeded" => Ok(AuditOutcome::Succeeded),
        "denied" => Ok(AuditOutcome::Denied),
        "failed" => Ok(AuditOutcome::Failed),
        other => Err(GatewayError::CorruptState(format!("unknown audit outcome {other:?}"))),
    }
}
