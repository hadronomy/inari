use jsonwebtoken::jwk::{Jwk, ThumbprintHash};
use sea_orm::sea_query::OnConflict;
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, DatabaseTransaction, EntityTrait, QueryFilter,
    QuerySelect, TransactionTrait,
};

use super::entity::value::{
    AgentKeyPurpose, InvitationState, StoredActions, StoredDispatchEncryptionKey, StoredJwk,
    StoredSnapshot,
};
use super::entity::{agent, agent_verification_key, invitation};
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
            public_jwk: Set(StoredJwk(enrollment.public_jwk.clone())),
            dispatch_key: Set(Some(StoredDispatchEncryptionKey(enrollment.dispatch_key))),
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
                .update_column(agent::COLUMN.dispatch_key)
                .update_column(agent::COLUMN.certificate_pem)
                .update_column(agent::COLUMN.namespace)
                .update_column(agent::COLUMN.protocol_version)
                .update_column(agent::COLUMN.controller_actions)
                .update_column(agent::COLUMN.last_enrolled_at)
                .to_owned(),
        )
        .exec(&transaction)
        .await?;

        for (purpose, jwk) in [
            (AgentKeyPurpose::TransportIdentity, enrollment.public_jwk),
            (AgentKeyPurpose::AgentState, enrollment.state_signing_jwk),
        ] {
            register_verification_key(
                &transaction,
                enrollment.agent_id.as_str(),
                purpose,
                jwk,
                enrolled_at,
            )
            .await?;
        }

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

async fn register_verification_key(
    transaction: &DatabaseTransaction,
    agent_id: &str,
    purpose: AgentKeyPurpose,
    public_jwk: Jwk,
    registered_at: chrono::DateTime<chrono::FixedOffset>,
) -> GatewayResult<()> {
    let key_id = public_jwk
        .common
        .key_id
        .clone()
        .ok_or_else(|| GatewayError::InvalidInput("verification keys require a kid".into()))?;
    let thumbprint = public_jwk.thumbprint(ThumbprintHash::SHA256);
    if let Some(existing) = agent_verification_key::Entity::find_by_id(&key_id)
        .one(transaction)
        .await?
    {
        if existing.agent_id != agent_id
            || existing.purpose != purpose
            || existing.jwk_thumbprint != thumbprint
            || existing.retired_at.is_some()
        {
            return Err(GatewayError::Conflict(
                "verification key identity is immutable and retirement is permanent".into(),
            ));
        }
    }
    agent_verification_key::Entity::update_many()
        .col_expr(
            agent_verification_key::Column::RetiredAt,
            sea_orm::sea_query::Expr::value(registered_at),
        )
        .filter(agent_verification_key::Column::AgentId.eq(agent_id))
        .filter(agent_verification_key::Column::Purpose.eq(purpose.clone()))
        .filter(agent_verification_key::Column::KeyId.ne(&key_id))
        .filter(agent_verification_key::Column::RetiredAt.is_null())
        .exec(transaction)
        .await?;
    agent_verification_key::Entity::insert(agent_verification_key::ActiveModel {
        key_id: Set(key_id.clone()),
        agent_id: Set(agent_id.to_owned()),
        purpose: Set(purpose.clone()),
        jwk_thumbprint: Set(thumbprint.clone()),
        public_jwk: Set(StoredJwk(public_jwk)),
        registered_at: Set(registered_at),
        retired_at: Set(None),
    })
    .on_conflict(
        OnConflict::new()
            .do_nothing()
            .to_owned(),
    )
    .try_insert()
    .exec(transaction)
    .await?;
    let registered = agent_verification_key::Entity::find_by_id(key_id)
        .one(transaction)
        .await?
        .ok_or_else(|| {
            GatewayError::Conflict("verification key already has another identity".into())
        })?;
    if registered.agent_id != agent_id
        || registered.purpose != purpose
        || registered.jwk_thumbprint != thumbprint
    {
        return Err(GatewayError::Conflict(
            "verification key owner and purpose are immutable".into(),
        ));
    }
    Ok(())
}
