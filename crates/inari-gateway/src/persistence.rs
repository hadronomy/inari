mod gateway_data;

use std::path::Path;
#[cfg(test)]
use std::str::FromStr;
use std::time::Duration;

use chrono::{DateTime, Utc};
use serde_json::Value;
use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteSynchronous};
use sqlx::{Row, SqlitePool};
use subtle::ConstantTimeEq;

use crate::onboarding::{InvitationCode, InvitationId, InvitationState, InvitationStatus};
use crate::protocol::{AgentPublication, GatewaySnapshot, ProtocolVersion};
use crate::{GatewayError, GatewayResult};

#[derive(Clone, Debug)]
pub struct GatewayRepository {
    pool: SqlitePool,
}

#[derive(Debug, Clone)]
pub struct AgentEnrollmentRecord {
    pub agent_id: String,
    pub key_id: String,
    pub jwk_thumbprint: String,
    pub public_jwk: Value,
    pub certificate_pem: Option<String>,
    pub namespace: String,
    pub protocol_version: ProtocolVersion,
    pub controller_actions: Vec<String>,
    pub enrolled_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub enum EnrollmentCredential {
    ConfiguredToken { digest: [u8; 32] },
    Invitation { invitation_id: String },
}

#[derive(Debug, Clone)]
pub struct PersistedCommand {
    pub agent_id: String,
    pub namespace: String,
    pub command_id: String,
    pub message_id: String,
    pub sequence: u64,
    pub state: String,
    pub command: Value,
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
    #[must_use]
    pub fn disconnected() -> Self {
        let options = SqliteConnectOptions::new().filename(":memory:");
        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_lazy_with(options);
        Self { pool }
    }

    pub async fn connect(path: impl AsRef<Path>) -> GatewayResult<Self> {
        let path = path.as_ref();
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let options = SqliteConnectOptions::new()
            .filename(path)
            .create_if_missing(true)
            .foreign_keys(true)
            .journal_mode(SqliteJournalMode::Wal)
            .synchronous(SqliteSynchronous::Normal)
            .busy_timeout(Duration::from_secs(5));
        let pool = SqlitePoolOptions::new()
            .max_connections(8)
            .connect_with(options)
            .await?;
        sqlx::migrate!().run(&pool).await?;
        set_owner_only_permissions(path).await?;
        Ok(Self { pool })
    }

    #[cfg(test)]
    pub async fn in_memory() -> GatewayResult<Self> {
        let options = SqliteConnectOptions::from_str("sqlite::memory:")?
            .foreign_keys(true)
            .journal_mode(SqliteJournalMode::Memory);
        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_with(options)
            .await?;
        sqlx::migrate!().run(&pool).await?;
        Ok(Self { pool })
    }

    #[must_use]
    pub fn pool(&self) -> &SqlitePool {
        &self.pool
    }

    pub async fn create_invitation(
        &self,
        code: &InvitationCode,
        label: Option<&str>,
        created_at: DateTime<Utc>,
        expires_at: DateTime<Utc>,
    ) -> GatewayResult<()> {
        sqlx::query(
            "INSERT INTO invitations (
                invitation_id, label, secret_digest, state, created_at, expires_at
             ) VALUES (?, ?, ?, 'created', ?, ?)",
        )
        .bind(code.id().as_str())
        .bind(
            label
                .map(str::trim)
                .filter(|value| !value.is_empty()),
        )
        .bind(code.secret_digest().as_slice())
        .bind(created_at)
        .bind(expires_at)
        .execute(&self.pool)
        .await?;
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
            "SELECT invitation_id, label, state, created_at, expires_at, claimed_at,
                    enrolled_at, online_at, revoked_at, failed_at, last_error,
                    bound_agent_id, bound_key_id, latest_snapshot_json
             FROM invitations WHERE invitation_id = ?",
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
             WHERE expires_at < ? AND state IN ('created', 'claimed', 'failed')",
        )
        .bind(now)
        .execute(&self.pool)
        .await?;
        let rows = sqlx::query(
            "SELECT invitation_id, label, state, created_at, expires_at, claimed_at,
                    enrolled_at, online_at, revoked_at, failed_at, last_error,
                    bound_agent_id, bound_key_id, latest_snapshot_json
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
    ) -> GatewayResult<InvitationStatus> {
        let result = sqlx::query(
            "UPDATE invitations SET state = 'revoked', revoked_at = ?
             WHERE invitation_id = ? AND state NOT IN ('online', 'revoked')",
        )
        .bind(now)
        .bind(invitation_id)
        .execute(&self.pool)
        .await?;
        if result.rows_affected() == 0 {
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
        self.invitation(invitation_id, now)
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
            "SELECT secret_digest, state, expires_at FROM invitations WHERE invitation_id = ?",
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
        sqlx::query("DELETE FROM invitation_attempts WHERE invitation_id = ? AND attempted_at < ?")
            .bind(code.id().as_str())
            .bind(cutoff)
            .execute(&mut *transaction)
            .await?;
        let failures: i64 =
            sqlx::query_scalar("SELECT COUNT(*) FROM invitation_attempts WHERE invitation_id = ?")
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
                "INSERT INTO invitation_attempts (invitation_id, attempted_at) VALUES (?, ?)",
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
             SET state = 'claimed', claimed_at = ?, bound_agent_id = ?, bound_key_id = ?
             WHERE invitation_id = ? AND state = 'created'",
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
        sqlx::query("DELETE FROM invitation_attempts WHERE invitation_id = ?")
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
             WHERE invitation_id = ? AND expires_at < ? AND state IN ('created', 'claimed', 'failed')",
        )
        .bind(invitation_id)
        .bind(now)
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

fn invitation_from_row(row: &sqlx::sqlite::SqliteRow) -> GatewayResult<InvitationStatus> {
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
    let snapshot: Option<String> = row.try_get("latest_snapshot_json")?;
    Ok(InvitationStatus {
        invitation_id,
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
        latest_snapshot: snapshot
            .map(|value| serde_json::from_str(&value))
            .transpose()?,
    })
}

#[cfg(unix)]
async fn set_owner_only_permissions(path: &Path) -> GatewayResult<()> {
    use std::os::unix::fs::PermissionsExt;

    let mut permissions = tokio::fs::metadata(path)
        .await?
        .permissions();
    permissions.set_mode(0o600);
    tokio::fs::set_permissions(path, permissions).await?;
    Ok(())
}

#[cfg(not(unix))]
async fn set_owner_only_permissions(_path: &Path) -> GatewayResult<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use chrono::Utc;

    use super::GatewayRepository;
    use crate::onboarding::{InvitationCode, InvitationState};

    #[tokio::test]
    async fn invitation_claim_is_transactional_and_one_use() {
        let repository = GatewayRepository::in_memory()
            .await
            .expect("repository should initialize");
        let code = InvitationCode::generate().expect("code should generate");
        let now = Utc::now();
        repository
            .create_invitation(&code, Some("Front desk"), now, now + chrono::Duration::minutes(10))
            .await
            .expect("invitation should persist");
        repository
            .claim_invitation(&code, "agt_test", "kid_test", now, Duration::from_secs(60), 5)
            .await
            .expect("first claim should succeed");
        assert!(
            repository
                .claim_invitation(&code, "agt_other", "kid_other", now, Duration::from_secs(60), 5,)
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
