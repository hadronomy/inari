use sqlx::Row;

use super::GatewayRepository;
use crate::audit::{AuditAction, AuditEvent, AuditEventDraft, AuditOutcome, AuditResource};
use crate::identity::ActorId;
use crate::protocol::OrganizationId;
use crate::{GatewayError, GatewayResult};

pub(super) async fn insert_audit_event(
    connection: &mut sqlx::PgConnection,
    draft: &AuditEventDraft,
) -> GatewayResult<()> {
    let (resource_kind, resource_id) = draft.resource.storage_parts();
    sqlx::query(
        "INSERT INTO audit_events (
            organization_id, actor_id, action, resource_kind, resource_id,
            outcome, request_id
         ) VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(draft.organization_id.as_str())
    .bind(draft.actor_id.as_str())
    .bind(draft.action.as_str())
    .bind(resource_kind)
    .bind(resource_id)
    .bind(draft.outcome.as_str())
    .bind(&draft.request_id)
    .execute(connection)
    .await?;
    Ok(())
}

impl GatewayRepository {
    pub async fn record_audit_event(&self, draft: &AuditEventDraft) -> GatewayResult<()> {
        let mut connection = self.pool.acquire().await?;
        insert_audit_event(&mut connection, draft).await?;
        Ok(())
    }

    pub async fn audit_events(
        &self,
        organization_id: &OrganizationId,
        before: Option<i64>,
        limit: u16,
    ) -> GatewayResult<Vec<AuditEvent>> {
        let rows = sqlx::query(
            "SELECT event_id, actor_id, action, resource_kind, resource_id,
                    outcome, request_id, occurred_at
             FROM audit_events
             WHERE organization_id = $1
               AND ($2::BIGINT IS NULL OR event_id < $2)
             ORDER BY event_id DESC
             LIMIT $3",
        )
        .bind(organization_id.as_str())
        .bind(before)
        .bind(i64::from(limit))
        .fetch_all(&self.pool)
        .await?;
        rows.iter()
            .map(|row| {
                let resource_kind: String = row.try_get("resource_kind")?;
                let resource_id: Option<String> = row.try_get("resource_id")?;
                Ok(AuditEvent {
                    event_id: row.try_get("event_id")?,
                    actor_id: ActorId::try_from(row.try_get::<String, _>("actor_id")?)?,
                    action: parse_action(&row.try_get::<String, _>("action")?)?,
                    resource: AuditResource::from_storage(&resource_kind, resource_id)?,
                    outcome: parse_outcome(&row.try_get::<String, _>("outcome")?)?,
                    request_id: row.try_get("request_id")?,
                    occurred_at: row.try_get("occurred_at")?,
                })
            })
            .collect()
    }
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
