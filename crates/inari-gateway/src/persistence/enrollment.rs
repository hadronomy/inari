use std::time::Duration;

use chrono::{DateTime, FixedOffset, SubsecRound, Utc};
use jsonwebtoken::jwk::{Jwk, ThumbprintHash};
use sea_orm::sea_query::OnConflict;
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, DatabaseTransaction, EntityTrait,
    PaginatorTrait, QueryFilter, QuerySelect, TransactionTrait,
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

use super::entity::value::{
    AgentKeyPurpose, InvitationState, StoredActions, StoredDispatchEncryptionKey, StoredJwk,
    StoredSnapshot,
};
use super::entity::{agent, agent_verification_key, device, invitation, invitation_attempt};
use super::{AgentEnrollmentRecord, GatewayRepository, PreparedEnrollment, stored_time, utc_time};
use crate::audit::{AuditAction, AuditEventDraft, AuditOutcome, AuditResource};
use crate::identity::ActorId;
use crate::onboarding::InvitationCode;
use crate::protocol::{
    DispatchEncryptionKey, GatewaySnapshot, OrganizationId, ProtocolVersion, SiteId,
};
use crate::{GatewayError, GatewayResult};

/// Throttles wrong invitation secrets. More than `max_failures` within `window`
/// rejects every attempt, including a correct secret, until the window passes.
#[derive(Debug, Clone, Copy)]
pub struct InvitationAttemptLimit {
    pub window: Duration,
    pub max_failures: usize,
}

impl GatewayRepository {
    /// Consumes an invitation and persists the enrolled Agent in one transaction.
    ///
    /// The invitation row stays locked from secret verification to commit, and
    /// the enrollment time is read after that lock is held. `prepare` runs
    /// after the secret, attempt limit, expiry, Organization, Site, and
    /// invitation state pass, and before enrollment writes. It holds the lock, so it
    /// must not wait on the network. An error from `prepare` or from a write
    /// rolls back the whole enrollment and the invitation stays `created`. A
    /// wrong secret is the one rejection that commits: the attempt counts
    /// against `limit`.
    ///
    /// An `enrolled` invitation replays only for its bound Agent with the same
    /// enrollment fingerprint and an unchanged Agent enrollment. A replay runs
    /// `prepare` again, writes nothing, and returns the original enrollment
    /// time. A different request from the bound Agent is a conflict. Another
    /// Agent is forbidden.
    ///
    /// Locks are taken in one order: the invitation, then the Agent, then its
    /// verification keys.
    pub async fn enroll_agent<T, F>(
        &self,
        code: &InvitationCode,
        enrollment: AgentEnrollmentRecord,
        snapshot: &GatewaySnapshot,
        limit: InvitationAttemptLimit,
        prepare: F,
    ) -> GatewayResult<PreparedEnrollment<T>>
    where
        F: FnOnce() -> GatewayResult<T>,
    {
        let transaction = self.database.begin().await?;
        let invitation = invitation::Entity::find_by_id(code.id().as_str())
            .lock_exclusive()
            .one(&transaction)
            .await?
            .ok_or_else(rejected_secret)?;
        // A request can wait on the lock past the expiry, so the time comes
        // after the lock. PostgreSQL keeps microseconds; truncating here makes
        // a replay return exactly the stored enrollment time.
        let now = Utc::now().trunc_subsecs(6);

        if recent_failures(&transaction, code, now, limit.window).await?
            >= u64::try_from(limit.max_failures.max(1)).unwrap_or(u64::MAX)
        {
            return Err(GatewayError::Forbidden("too many failed invitation attempts".into()));
        }
        let candidate = code.secret_digest();
        if !bool::from(
            invitation
                .secret_digest
                .as_slice()
                .ct_eq(candidate.as_slice()),
        ) {
            invitation_attempt::ActiveModel {
                invitation_id: Set(invitation.invitation_id),
                attempted_at: Set(stored_time(now)),
            }
            .insert(&transaction)
            .await?;
            transaction.commit().await?;
            return Err(rejected_secret());
        }
        if now >= utc_time(invitation.expires_at) {
            return Err(unavailable());
        }
        if invitation.organization_id != enrollment.organization_id.as_str()
            || invitation.site_id != enrollment.site_id.as_str()
        {
            return Err(GatewayError::Forbidden(
                "enrollment invitation belongs to another Organization or Site".into(),
            ));
        }

        let fingerprint = enrollment_fingerprint(&enrollment)?;
        match invitation.state {
            InvitationState::Created => {
                let value = prepare()?;
                persist_enrollment(
                    &transaction,
                    invitation,
                    enrollment,
                    snapshot,
                    fingerprint,
                    now,
                )
                .await?;
                transaction.commit().await?;
                Ok(PreparedEnrollment { value, enrolled_at: now })
            },
            InvitationState::Enrolled => {
                let enrolled_at =
                    replayed_enrollment(&transaction, &invitation, &enrollment, &fingerprint)
                        .await?;
                if Utc::now() >= utc_time(invitation.expires_at) {
                    return Err(unavailable());
                }
                let value = prepare()?;
                transaction.commit().await?;
                Ok(PreparedEnrollment { value, enrolled_at })
            },
            InvitationState::Online
            | InvitationState::Expired
            | InvitationState::Failed
            | InvitationState::Revoked => Err(unavailable()),
        }
    }
}

fn rejected_secret() -> GatewayError {
    GatewayError::Forbidden("enrollment invitation was not accepted".into())
}

fn unavailable() -> GatewayError {
    GatewayError::Forbidden("enrollment invitation is unavailable".into())
}

async fn recent_failures(
    transaction: &DatabaseTransaction,
    code: &InvitationCode,
    now: DateTime<Utc>,
    window: Duration,
) -> GatewayResult<u64> {
    let cutoff = now
        - chrono::Duration::from_std(window).map_err(|_| {
            GatewayError::InvalidInput("failed-attempt window is out of range".into())
        })?;
    invitation_attempt::Entity::delete_many()
        .filter(
            invitation_attempt::COLUMN
                .invitation_id
                .eq(code.id().as_str()),
        )
        .filter(
            invitation_attempt::COLUMN
                .attempted_at
                .lt(stored_time(cutoff)),
        )
        .exec(transaction)
        .await?;
    Ok(invitation_attempt::Entity::find()
        .filter(
            invitation_attempt::COLUMN
                .invitation_id
                .eq(code.id().as_str()),
        )
        .count(transaction)
        .await?)
}

/// Identifies the validated facts that an enrollment response depends on.
///
/// A retry with the same fingerprint must receive an equivalent response. The
/// enrollment time and the snapshot are excluded: they change between honest
/// retries and do not change the authorization.
fn enrollment_fingerprint(enrollment: &AgentEnrollmentRecord) -> GatewayResult<[u8; 32]> {
    #[derive(Serialize)]
    struct Facts<'a> {
        agent_id: &'a str,
        key_id: &'a str,
        csr_fingerprint: &'a str,
        transport_key_thumbprint: &'a str,
        state_key_thumbprint: String,
        dispatch_key: &'a DispatchEncryptionKey,
        organization_id: &'a OrganizationId,
        site_id: &'a SiteId,
        protocol_version: &'a ProtocolVersion,
        namespace: &'a str,
        controller_actions: &'a [String],
    }

    let facts = Facts {
        agent_id: enrollment.agent_id.as_str(),
        key_id: &enrollment.key_id,
        csr_fingerprint: &enrollment.csr_fingerprint,
        transport_key_thumbprint: &enrollment.jwk_thumbprint,
        state_key_thumbprint: enrollment
            .state_signing_jwk
            .thumbprint(ThumbprintHash::SHA256),
        dispatch_key: &enrollment.dispatch_key,
        organization_id: &enrollment.organization_id,
        site_id: &enrollment.site_id,
        protocol_version: &enrollment.protocol_version,
        namespace: &enrollment.namespace,
        controller_actions: &enrollment.controller_actions,
    };
    Ok(Sha256::digest(serde_json_canonicalizer::to_vec(&facts)?).into())
}

async fn replayed_enrollment(
    transaction: &DatabaseTransaction,
    invitation: &invitation::Model,
    enrollment: &AgentEnrollmentRecord,
    fingerprint: &[u8; 32],
) -> GatewayResult<DateTime<Utc>> {
    if invitation.bound_agent_id.as_deref() != Some(enrollment.agent_id.as_str()) {
        return Err(GatewayError::Forbidden(
            "enrollment invitation is bound to another Agent".into(),
        ));
    }
    let Some(stored) = invitation
        .enrollment_fingerprint
        .as_deref()
    else {
        // Enrollments from before fingerprints cannot prove the original CSR.
        return Err(GatewayError::Conflict(
            "this enrollment cannot be retried; create a new invitation".into(),
        ));
    };
    if invitation.bound_key_id.as_deref() != Some(enrollment.key_id.as_str())
        || stored != fingerprint.as_slice()
    {
        return Err(GatewayError::Conflict(
            "enrollment retry does not match the original request".into(),
        ));
    }
    let enrolled_at = invitation
        .enrolled_at
        .ok_or_else(|| GatewayError::CorruptState("enrolled invitation has no time".into()))?;
    // A later invitation can rotate the Agent keys. Replaying this one would
    // hand back credentials for keys that are already retired. The shared
    // lock makes a concurrent rotation wait until this replay commits.
    let current = agent::Entity::find_by_id(enrollment.agent_id.as_str())
        .lock_shared()
        .one(transaction)
        .await?;
    if !current.is_some_and(|agent| {
        agent.key_id == enrollment.key_id && agent.last_enrolled_at == enrolled_at
    }) {
        return Err(GatewayError::Conflict("a later enrollment replaced this invitation".into()));
    }
    Ok(utc_time(enrolled_at))
}

async fn persist_enrollment(
    transaction: &DatabaseTransaction,
    invitation: invitation::Model,
    enrollment: AgentEnrollmentRecord,
    snapshot: &GatewaySnapshot,
    fingerprint: [u8; 32],
    enrolled_at: DateTime<Utc>,
) -> GatewayResult<()> {
    let inventory = super::inventory::InventoryProjection::parse(&snapshot.runtime.inventory)?;
    let enrolled_at = stored_time(enrolled_at);
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
            .update_column(agent::COLUMN.namespace)
            .update_column(agent::COLUMN.protocol_version)
            .update_column(agent::COLUMN.controller_actions)
            .update_column(agent::COLUMN.last_enrolled_at)
            .to_owned(),
    )
    .exec(transaction)
    .await?;

    device::Entity::delete_many()
        .filter(device::Column::AgentId.eq(enrollment.agent_id.as_str()))
        .exec(transaction)
        .await?;
    inventory
        .apply(
            transaction,
            enrollment.agent_id.as_str(),
            enrollment.site_id.as_str(),
            snapshot.generated_at,
        )
        .await?;

    for (purpose, jwk) in [
        (AgentKeyPurpose::TransportIdentity, enrollment.public_jwk),
        (AgentKeyPurpose::AgentState, enrollment.state_signing_jwk),
    ] {
        register_verification_key(
            transaction,
            enrollment.agent_id.as_str(),
            purpose,
            jwk,
            enrolled_at,
        )
        .await?;
    }

    invitation_attempt::Entity::delete_many()
        .filter(
            invitation_attempt::COLUMN
                .invitation_id
                .eq(&invitation.invitation_id),
        )
        .exec(transaction)
        .await?;
    let mut update: invitation::ActiveModel = invitation.into();
    update.state = Set(InvitationState::Enrolled);
    update.enrolled_at = Set(Some(enrolled_at));
    update.bound_agent_id = Set(Some(enrollment.agent_id.as_str().to_owned()));
    update.bound_key_id = Set(Some(enrollment.key_id));
    update.enrollment_fingerprint = Set(Some(fingerprint.to_vec()));
    update.latest_snapshot = Set(Some(StoredSnapshot(snapshot.clone())));
    update.update(transaction).await?;

    let agent_id = enrollment.agent_id;
    super::audit::insert_audit_event(
        transaction,
        &AuditEventDraft {
            organization_id: enrollment.organization_id,
            actor_id: ActorId::from_agent(&agent_id),
            action: AuditAction::AgentEnrolled,
            resource: AuditResource::Agent { agent_id },
            outcome: AuditOutcome::Succeeded,
            request_id: None,
        },
    )
    .await
}

async fn register_verification_key(
    transaction: &DatabaseTransaction,
    agent_id: &str,
    purpose: AgentKeyPurpose,
    public_jwk: Jwk,
    registered_at: DateTime<FixedOffset>,
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
        && (existing.agent_id != agent_id
            || existing.purpose != purpose
            || existing.jwk_thumbprint != thumbprint
            || existing.retired_at.is_some())
    {
        return Err(GatewayError::Conflict(
            "verification key identity is immutable and retirement is permanent".into(),
        ));
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
