use chrono::{DateTime, Utc};
use sea_orm::sea_query::{Expr, OnConflict};
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, EntityTrait, QueryFilter, QueryOrder,
    TransactionTrait,
};

use super::entity::value::InvitationState as StoredInvitationState;
use super::entity::{invitation, organization, site};
use super::{GatewayRepository, stored_time, utc_time};
use crate::audit::{AuditAction, AuditContext, AuditEventDraft, AuditOutcome, AuditResource};
use crate::onboarding::{InvitationCode, InvitationId, InvitationState, InvitationStatus};
use crate::protocol::{OrganizationId, SiteId};
use crate::{GatewayError, GatewayResult};

impl GatewayRepository {
    pub async fn ensure_organization(
        &self,
        organization_id: &OrganizationId,
        organization_name: &str,
        site_id: &SiteId,
        site_name: &str,
    ) -> GatewayResult<()> {
        let transaction = self.database.begin().await?;
        let now = stored_time(Utc::now());
        organization::Entity::insert(organization::ActiveModel {
            organization_id: Set(organization_id.as_str().to_owned()),
            name: Set(organization_name.to_owned()),
            created_at: Set(now),
            updated_at: Set(now),
        })
        .on_conflict(
            OnConflict::column(organization::COLUMN.organization_id)
                .update_column(organization::COLUMN.name)
                .update_column(organization::COLUMN.updated_at)
                .to_owned(),
        )
        .exec(&transaction)
        .await?;
        site::Entity::insert(site::ActiveModel {
            site_id: Set(site_id.as_str().to_owned()),
            organization_id: Set(organization_id.as_str().to_owned()),
            name: Set(site_name.to_owned()),
            created_at: Set(now),
            updated_at: Set(now),
        })
        .on_conflict(
            OnConflict::column(site::COLUMN.site_id)
                .update_column(site::COLUMN.name)
                .update_column(site::COLUMN.updated_at)
                .to_owned(),
        )
        .exec(&transaction)
        .await?;
        transaction.commit().await?;
        Ok(())
    }

    pub async fn create_invitation(
        &self,
        code: &InvitationCode,
        organization_id: &OrganizationId,
        site_id: &SiteId,
        label: Option<&str>,
        validity: std::ops::Range<DateTime<Utc>>,
        audit: &AuditContext,
    ) -> GatewayResult<()> {
        let transaction = self.database.begin().await?;
        invitation::ActiveModel {
            invitation_id: Set(code.id().as_str().to_owned()),
            organization_id: Set(organization_id.as_str().to_owned()),
            site_id: Set(site_id.as_str().to_owned()),
            label: Set(label
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)),
            secret_digest: Set(code.secret_digest().to_vec()),
            state: Set(StoredInvitationState::Created),
            created_at: Set(stored_time(validity.start)),
            expires_at: Set(stored_time(validity.end)),
            ..Default::default()
        }
        .insert(&transaction)
        .await?;
        super::audit::insert_audit_event(
            &transaction,
            &AuditEventDraft {
                organization_id: organization_id.clone(),
                actor_id: audit.actor_id.clone(),
                action: AuditAction::InvitationCreated,
                resource: AuditResource::Invitation { invitation_id: code.id().clone() },
                outcome: AuditOutcome::Succeeded,
                request_id: audit.request_id.clone(),
            },
        )
        .await?;
        transaction.commit().await?;
        Ok(())
    }

    pub async fn invitation(
        &self,
        invitation_id: &str,
        now: DateTime<Utc>,
    ) -> GatewayResult<InvitationStatus> {
        self.expire_invitation(invitation_id, now)
            .await?;
        invitation::Entity::find_by_id(invitation_id)
            .one(&self.database)
            .await?
            .ok_or_else(|| GatewayError::NotFound("onboarding invitation was not found".into()))?
            .try_into()
    }

    pub async fn invitations(&self, now: DateTime<Utc>) -> GatewayResult<Vec<InvitationStatus>> {
        invitation::Entity::update_many()
            .col_expr(invitation::COLUMN.state, Expr::value(StoredInvitationState::Expired))
            .filter(
                invitation::COLUMN
                    .expires_at
                    .lt(stored_time(now)),
            )
            .filter(
                invitation::COLUMN
                    .state
                    .is_in(expirable_states()),
            )
            .exec(&self.database)
            .await?;
        invitation::Entity::find()
            .order_by_desc(invitation::COLUMN.created_at)
            .all(&self.database)
            .await?
            .into_iter()
            .map(TryInto::try_into)
            .collect()
    }

    pub async fn revoke_invitation(
        &self,
        invitation_id: &str,
        now: DateTime<Utc>,
        organization_id: &OrganizationId,
        audit: &AuditContext,
    ) -> GatewayResult<InvitationStatus> {
        let transaction = self.database.begin().await?;
        let result = invitation::Entity::update_many()
            .col_expr(invitation::COLUMN.state, Expr::value(StoredInvitationState::Revoked))
            .col_expr(invitation::COLUMN.revoked_at, Expr::value(stored_time(now)))
            .filter(
                invitation::COLUMN
                    .invitation_id
                    .eq(invitation_id),
            )
            .filter(
                invitation::COLUMN
                    .state
                    .is_not_in([StoredInvitationState::Online, StoredInvitationState::Revoked]),
            )
            .exec(&transaction)
            .await?;
        if result.rows_affected == 0 {
            transaction.rollback().await?;
            let current = self
                .invitation(invitation_id, now)
                .await?;
            return match current.state {
                InvitationState::Online => Err(GatewayError::Conflict(
                    "an online enrollment cannot be revoked through its invitation".into(),
                )),
                InvitationState::Revoked => Ok(current),
                _ => Err(GatewayError::Conflict("invitation could not be revoked".into())),
            };
        }
        let invitation_id = invitation_id.parse::<InvitationId>()?;
        super::audit::insert_audit_event(
            &transaction,
            &AuditEventDraft {
                organization_id: organization_id.clone(),
                actor_id: audit.actor_id.clone(),
                action: AuditAction::InvitationRevoked,
                resource: AuditResource::Invitation { invitation_id: invitation_id.clone() },
                outcome: AuditOutcome::Succeeded,
                request_id: audit.request_id.clone(),
            },
        )
        .await?;
        transaction.commit().await?;
        self.invitation(invitation_id.as_str(), now)
            .await
    }

    async fn expire_invitation(
        &self,
        invitation_id: &str,
        now: DateTime<Utc>,
    ) -> GatewayResult<()> {
        invitation::Entity::update_many()
            .col_expr(invitation::COLUMN.state, Expr::value(StoredInvitationState::Expired))
            .filter(
                invitation::COLUMN
                    .invitation_id
                    .eq(invitation_id),
            )
            .filter(
                invitation::COLUMN
                    .expires_at
                    .lt(stored_time(now)),
            )
            .filter(
                invitation::COLUMN
                    .state
                    .is_in(expirable_states()),
            )
            .exec(&self.database)
            .await?;
        Ok(())
    }
}

impl TryFrom<invitation::Model> for InvitationStatus {
    type Error = GatewayError;

    fn try_from(model: invitation::Model) -> Result<Self, Self::Error> {
        Ok(Self {
            invitation_id: model.invitation_id.parse()?,
            site_id: model.site_id.parse()?,
            label: model.label,
            state: model.state.into(),
            created_at: utc_time(model.created_at),
            expires_at: utc_time(model.expires_at),
            enrolled_at: model.enrolled_at.map(utc_time),
            online_at: model.online_at.map(utc_time),
            revoked_at: model.revoked_at.map(utc_time),
            failed_at: model.failed_at.map(utc_time),
            last_error: model.last_error,
            agent_id: model.bound_agent_id,
            key_id: model.bound_key_id,
            latest_snapshot: model
                .latest_snapshot
                .map(|snapshot| snapshot.0),
        })
    }
}

impl From<StoredInvitationState> for InvitationState {
    fn from(value: StoredInvitationState) -> Self {
        match value {
            StoredInvitationState::Created => Self::Created,
            StoredInvitationState::Enrolled => Self::Enrolled,
            StoredInvitationState::Online => Self::Online,
            StoredInvitationState::Expired => Self::Expired,
            StoredInvitationState::Failed => Self::Failed,
            StoredInvitationState::Revoked => Self::Revoked,
        }
    }
}

fn expirable_states() -> [StoredInvitationState; 2] {
    [StoredInvitationState::Created, StoredInvitationState::Failed]
}
