mod audit;
mod fleet;
mod gateway_data;

use std::str::FromStr;
use std::time::Duration;

use chrono::{DateTime, Utc};
use jsonwebtoken::jwk::Jwk;
use secrecy::{ExposeSecret, SecretString};
use sqlx::postgres::{PgConnectOptions, PgPoolOptions, PgRow};
use sqlx::{PgPool, Row};
use subtle::ConstantTimeEq;

use crate::audit::{AuditAction, AuditContext, AuditEventDraft, AuditOutcome, AuditResource};
use crate::onboarding::{InvitationCode, InvitationId, InvitationState, InvitationStatus};
use crate::protocol::{
    AgentPublication, ControllerCommand, GatewaySnapshot, OrganizationId, ProtocolVersion, SiteId,
};
use crate::{GatewayError, GatewayResult};

#[derive(Clone, Debug)]
pub struct GatewayRepository {
    pub(super) pool: PgPool,
}

#[derive(Debug, Clone)]
pub struct AgentEnrollmentRecord {
    pub agent_id: String,
    pub organization_id: OrganizationId,
    pub site_id: SiteId,
    pub key_id: String,
    pub jwk_thumbprint: String,
    pub public_jwk: Jwk,
    pub certificate_pem: Option<String>,
    pub namespace: String,
    pub protocol_version: ProtocolVersion,
    pub controller_actions: Vec<String>,
    pub enrolled_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct PersistedCommand {
    pub agent_id: String,
    pub namespace: String,
    pub command_id: String,
    pub message_id: String,
    pub sequence: u64,
    pub state: String,
    pub command: ControllerCommand,
    pub issued_at: DateTime<Utc>,
    pub published_at: Option<DateTime<Utc>>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct PersistedPublication {
    pub key: String,
    pub received_at: DateTime<Utc>,
    pub message: AgentPublication,
}

#[derive(Debug, Clone)]
pub struct PersistedAgentStatus {
    pub message_id: String,
    pub received_at: DateTime<Utc>,
    pub snapshot: GatewaySnapshot,
}

impl GatewayRepository {
    /// A lazy repository for application states that have the managed gateway disabled.
    /// Any attempted query fails instead of silently persisting to a local fallback.
    #[must_use]
    pub fn disconnected() -> Self {
        let options = PgConnectOptions::new()
            .host("127.0.0.1")
            .database("inari_gateway_disabled");
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect_lazy_with(options);
        Self { pool }
    }

    pub async fn connect(
        database_url: &SecretString,
        min_connections: u32,
        max_connections: u32,
    ) -> GatewayResult<Self> {
        let options = PgConnectOptions::from_str(database_url.expose_secret())?;
        let pool = PgPoolOptions::new()
            .max_connections(max_connections)
            .min_connections(min_connections)
            .connect_with(options)
            .await?;
        Ok(Self { pool })
    }

    pub async fn migrate(database_url: &SecretString) -> GatewayResult<()> {
        let options = PgConnectOptions::from_str(database_url.expose_secret())?;
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect_with(options)
            .await?;
        sqlx::migrate!().run(&pool).await?;
        pool.close().await;
        Ok(())
    }

    #[must_use]
    pub fn pool(&self) -> &PgPool {
        &self.pool
    }

    pub async fn ensure_organization(
        &self,
        organization_id: &OrganizationId,
        organization_name: &str,
        site_id: &SiteId,
        site_name: &str,
    ) -> GatewayResult<()> {
        let mut transaction = self.pool.begin().await?;
        sqlx::query(
            "INSERT INTO organizations (organization_id, name)
             VALUES ($1, $2)
             ON CONFLICT (organization_id) DO UPDATE
             SET name = EXCLUDED.name, updated_at = NOW()",
        )
        .bind(organization_id.as_str())
        .bind(organization_name)
        .execute(&mut *transaction)
        .await?;
        sqlx::query(
            "INSERT INTO sites (site_id, organization_id, name)
             VALUES ($1, $2, $3)
             ON CONFLICT (site_id) DO UPDATE
             SET name = EXCLUDED.name, updated_at = NOW()",
        )
        .bind(site_id.as_str())
        .bind(organization_id.as_str())
        .bind(site_name)
        .execute(&mut *transaction)
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
        let mut transaction = self.pool.begin().await?;
        sqlx::query(
            "INSERT INTO invitations (
                invitation_id, organization_id, site_id, label, secret_digest,
                state, created_at, expires_at
             ) VALUES ($1, $2, $3, $4, $5, 'created', $6, $7)",
        )
        .bind(code.id().as_str())
        .bind(organization_id.as_str())
        .bind(site_id.as_str())
        .bind(
            label
                .map(str::trim)
                .filter(|value| !value.is_empty()),
        )
        .bind(code.secret_digest().as_slice())
        .bind(validity.start)
        .bind(validity.end)
        .execute(&mut *transaction)
        .await?;
        audit::insert_audit_event(
            &mut transaction,
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
        let row = sqlx::query(
            "SELECT invitation_id, site_id, label, state, created_at, expires_at, claimed_at,
                    enrolled_at, online_at, revoked_at, failed_at, last_error,
                    bound_agent_id, bound_key_id, latest_snapshot
             FROM invitations WHERE invitation_id = $1",
        )
        .bind(invitation_id)
        .fetch_optional(&self.pool)
        .await?
        .ok_or_else(|| GatewayError::NotFound("onboarding invitation was not found".into()))?;
        invitation_from_row(&row)
    }

    pub async fn invitations(&self, now: DateTime<Utc>) -> GatewayResult<Vec<InvitationStatus>> {
        sqlx::query(
            "UPDATE invitations SET state = 'expired'
             WHERE expires_at < $1 AND state IN ('created', 'claimed', 'failed')",
        )
        .bind(now)
        .execute(&self.pool)
        .await?;
        let rows = sqlx::query(
            "SELECT invitation_id, site_id, label, state, created_at, expires_at, claimed_at,
                    enrolled_at, online_at, revoked_at, failed_at, last_error,
                    bound_agent_id, bound_key_id, latest_snapshot
             FROM invitations ORDER BY created_at DESC",
        )
        .fetch_all(&self.pool)
        .await?;
        rows.iter()
            .map(invitation_from_row)
            .collect()
    }

    pub async fn revoke_invitation(
        &self,
        invitation_id: &str,
        now: DateTime<Utc>,
        organization_id: &OrganizationId,
        audit: &AuditContext,
    ) -> GatewayResult<InvitationStatus> {
        let mut transaction = self.pool.begin().await?;
        let result = sqlx::query(
            "UPDATE invitations SET state = 'revoked', revoked_at = $1
             WHERE invitation_id = $2 AND state NOT IN ('online', 'revoked')",
        )
        .bind(now)
        .bind(invitation_id)
        .execute(&mut *transaction)
        .await?;
        if result.rows_affected() == 0 {
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
        let invitation_id: InvitationId = invitation_id.parse()?;
        audit::insert_audit_event(
            &mut transaction,
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

    pub async fn claim_invitation(
        &self,
        code: &InvitationCode,
        agent_id: &str,
        key_id: &str,
        now: DateTime<Utc>,
        failed_attempt_window: Duration,
        max_failed_attempts: usize,
    ) -> GatewayResult<()> {
        let mut transaction = self.pool.begin().await?;
        let row = sqlx::query(
            "SELECT secret_digest, state, expires_at
             FROM invitations WHERE invitation_id = $1 FOR UPDATE",
        )
        .bind(code.id().as_str())
        .fetch_optional(&mut *transaction)
        .await?
        .ok_or_else(|| GatewayError::Forbidden("enrollment invitation was not accepted".into()))?;
        let digest: Vec<u8> = row.try_get("secret_digest")?;
        let state: String = row.try_get("state")?;
        let expires_at: DateTime<Utc> = row.try_get("expires_at")?;
        if state != "created" || now >= expires_at {
            return Err(GatewayError::Forbidden("enrollment invitation is unavailable".into()));
        }
        let cutoff = now
            - chrono::Duration::from_std(failed_attempt_window).map_err(|_| {
                GatewayError::InvalidInput("failed-attempt window is out of range".into())
            })?;
        sqlx::query(
            "DELETE FROM invitation_attempts
             WHERE invitation_id = $1 AND attempted_at < $2",
        )
        .bind(code.id().as_str())
        .bind(cutoff)
        .execute(&mut *transaction)
        .await?;
        let failures: i64 =
            sqlx::query_scalar("SELECT COUNT(*) FROM invitation_attempts WHERE invitation_id = $1")
                .bind(code.id().as_str())
                .fetch_one(&mut *transaction)
                .await?;
        if failures >= i64::try_from(max_failed_attempts.max(1)).unwrap_or(i64::MAX) {
            return Err(GatewayError::Forbidden("too many failed invitation attempts".into()));
        }
        let candidate = code.secret_digest();
        if digest.len() != candidate.len()
            || !bool::from(
                digest
                    .as_slice()
                    .ct_eq(candidate.as_slice()),
            )
        {
            sqlx::query(
                "INSERT INTO invitation_attempts (invitation_id, attempted_at) VALUES ($1, $2)",
            )
            .bind(code.id().as_str())
            .bind(now)
            .execute(&mut *transaction)
            .await?;
            transaction.commit().await?;
            return Err(GatewayError::Forbidden("enrollment invitation was not accepted".into()));
        }
        let result = sqlx::query(
            "UPDATE invitations
             SET state = 'claimed', claimed_at = $1, bound_agent_id = $2, bound_key_id = $3
             WHERE invitation_id = $4 AND state = 'created'",
        )
        .bind(now)
        .bind(agent_id)
        .bind(key_id)
        .bind(code.id().as_str())
        .execute(&mut *transaction)
        .await?;
        if result.rows_affected() != 1 {
            return Err(GatewayError::Conflict(
                "enrollment invitation was consumed concurrently".into(),
            ));
        }
        sqlx::query("DELETE FROM invitation_attempts WHERE invitation_id = $1")
            .bind(code.id().as_str())
            .execute(&mut *transaction)
            .await?;
        transaction.commit().await?;
        Ok(())
    }

    async fn expire_invitation(
        &self,
        invitation_id: &str,
        now: DateTime<Utc>,
    ) -> GatewayResult<()> {
        sqlx::query(
            "UPDATE invitations SET state = 'expired'
             WHERE invitation_id = $1 AND expires_at < $2
               AND state IN ('created', 'claimed', 'failed')",
        )
        .bind(invitation_id)
        .bind(now)
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

fn invitation_from_row(row: &PgRow) -> GatewayResult<InvitationStatus> {
    let invitation_id = row
        .try_get::<String, _>("invitation_id")?
        .parse::<InvitationId>()
        .map_err(|_| {
            GatewayError::Persistence(sqlx::Error::Protocol(
                "stored invitation id is invalid".into(),
            ))
        })?;
    let state: String = row.try_get("state")?;
    let state = match state.as_str() {
        "created" => InvitationState::Created,
        "claimed" => InvitationState::Claimed,
        "enrolled" => InvitationState::Enrolled,
        "online" => InvitationState::Online,
        "expired" => InvitationState::Expired,
        "failed" => InvitationState::Failed,
        "revoked" => InvitationState::Revoked,
        _ => {
            return Err(GatewayError::Persistence(sqlx::Error::Protocol(format!(
                "invalid invitation state {state:?}"
            ))));
        },
    };
    Ok(InvitationStatus {
        invitation_id,
        site_id: row
            .try_get::<String, _>("site_id")?
            .parse::<SiteId>()?,
        label: row.try_get("label")?,
        state,
        created_at: row.try_get("created_at")?,
        expires_at: row.try_get("expires_at")?,
        claimed_at: row.try_get("claimed_at")?,
        enrolled_at: row.try_get("enrolled_at")?,
        online_at: row.try_get("online_at")?,
        revoked_at: row.try_get("revoked_at")?,
        failed_at: row.try_get("failed_at")?,
        last_error: row.try_get("last_error")?,
        agent_id: row.try_get("bound_agent_id")?,
        key_id: row.try_get("bound_key_id")?,
        latest_snapshot: row
            .try_get::<Option<serde_json::Value>, _>("latest_snapshot")?
            .map(serde_json::from_value)
            .transpose()?,
    })
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use chrono::Utc;
    use secrecy::SecretString;

    use super::GatewayRepository;
    use crate::audit::AuditContext;
    use crate::identity::ActorId;
    use crate::onboarding::{InvitationCode, InvitationState};

    #[tokio::test]
    #[ignore = "requires INARI_TEST_DATABASE_URL"]
    async fn invitation_claim_is_transactional_and_one_use() {
        let database_url = std::env::var("INARI_TEST_DATABASE_URL")
            .expect("INARI_TEST_DATABASE_URL must be set for PostgreSQL integration tests");
        let repository = GatewayRepository::connect(&SecretString::from(database_url), 1, 4)
            .await
            .expect("repository should initialize");
        let code = InvitationCode::generate().expect("code should generate");
        let organization_id = "org_test"
            .parse()
            .expect("organization ID should parse");
        let site_id = "site_test"
            .parse()
            .expect("site ID should parse");
        repository
            .ensure_organization(&organization_id, "Test organization", &site_id, "Test site")
            .await
            .expect("organization should persist");
        let now = Utc::now();
        let audit = AuditContext::new(ActorId::from_oidc_subject("test-operator"), None);
        repository
            .create_invitation(
                &code,
                &organization_id,
                &site_id,
                Some("Front desk"),
                now..now + chrono::Duration::minutes(10),
                &audit,
            )
            .await
            .expect("invitation should persist");
        repository
            .claim_invitation(&code, "agt_test", "kid_test", now, Duration::from_secs(60), 5)
            .await
            .expect("first claim should succeed");
        assert!(
            repository
                .claim_invitation(&code, "agt_other", "kid_other", now, Duration::from_secs(60), 5)
                .await
                .is_err()
        );
        let status = repository
            .invitation(code.id().as_str(), now)
            .await
            .expect("invitation should load");
        assert_eq!(status.state, InvitationState::Claimed);
    }
}
