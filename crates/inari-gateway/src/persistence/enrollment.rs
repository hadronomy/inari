use sea_orm::sea_query::OnConflict;
use sea_orm::{ActiveModelTrait, ActiveValue::Set, EntityTrait, QuerySelect, TransactionTrait};

use super::entity::value::{InvitationState, StoredActions, StoredJwk, StoredSnapshot};
use super::entity::{agent, invitation};
use super::{AgentEnrollmentRecord, GatewayRepository, stored_time};
use crate::audit::{AuditAction, AuditEventDraft, AuditOutcome, AuditResource};
use crate::identity::ActorId;
use crate::protocol::GatewaySnapshot;
use crate::{GatewayError, GatewayResult};

impl GatewayRepository {
    pub async fn enroll_agent(
        &self,
        enrollment: AgentEnrollmentRecord,
        invitation_id: &str,
        snapshot: &GatewaySnapshot,
    ) -> GatewayResult<()> {
        let transaction = self.database.begin().await?;
        let mut invitation = invitation::Entity::find_by_id(invitation_id)
            .lock_exclusive()
            .one(&transaction)
            .await?
            .ok_or_else(|| {
                GatewayError::Forbidden("invitation is not claimed by this agent identity".into())
            })?;
        if invitation.state != InvitationState::Claimed
            || invitation.bound_agent_id.as_deref() != Some(enrollment.agent_id.as_str())
            || invitation.bound_key_id.as_deref() != Some(&enrollment.key_id)
        {
            return Err(GatewayError::Forbidden(
                "invitation is not claimed by this agent identity".into(),
            ));
        }

        let enrolled_at = stored_time(enrollment.enrolled_at);
        agent::Entity::insert(agent::ActiveModel {
            agent_id: Set(enrollment.agent_id.as_str().to_owned()),
            organization_id: Set(enrollment
                .organization_id
                .as_str()
                .to_owned()),
            site_id: Set(enrollment.site_id.as_str().to_owned()),
            key_id: Set(enrollment.key_id.clone()),
            jwk_thumbprint: Set(enrollment.jwk_thumbprint),
            public_jwk: Set(StoredJwk(enrollment.public_jwk)),
            certificate_pem: Set(enrollment.certificate_pem),
            namespace: Set(enrollment.namespace),
            protocol_version: Set(enrollment
                .protocol_version
                .as_str()
                .to_owned()),
            controller_actions: Set(StoredActions(enrollment.controller_actions)),
            enrolled_at: Set(enrolled_at),
            last_enrolled_at: Set(enrolled_at),
        })
        .on_conflict(
            OnConflict::column(agent::COLUMN.agent_id)
                .update_column(agent::COLUMN.organization_id)
                .update_column(agent::COLUMN.site_id)
                .update_column(agent::COLUMN.key_id)
                .update_column(agent::COLUMN.jwk_thumbprint)
                .update_column(agent::COLUMN.public_jwk)
                .update_column(agent::COLUMN.certificate_pem)
                .update_column(agent::COLUMN.namespace)
                .update_column(agent::COLUMN.protocol_version)
                .update_column(agent::COLUMN.controller_actions)
                .update_column(agent::COLUMN.last_enrolled_at)
                .to_owned(),
        )
        .exec(&transaction)
        .await?;

        invitation.state = InvitationState::Enrolled;
        invitation.enrolled_at = Some(enrolled_at);
        invitation.latest_snapshot = Some(StoredSnapshot(snapshot.clone()));
        invitation::ActiveModel::from(invitation)
            .update(&transaction)
            .await?;
        let agent_id = enrollment.agent_id.clone();
        super::audit::insert_audit_event(
            &transaction,
            &AuditEventDraft {
                organization_id: enrollment.organization_id,
                actor_id: ActorId::from_agent(&agent_id),
                action: AuditAction::AgentEnrolled,
                resource: AuditResource::Agent { agent_id },
                outcome: AuditOutcome::Succeeded,
                request_id: None,
            },
        )
        .await?;
        transaction.commit().await?;
        Ok(())
    }
}
