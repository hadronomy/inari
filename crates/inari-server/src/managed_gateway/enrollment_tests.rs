use std::cell::Cell;
use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{DateTime, Utc};
use ed25519_dalek::SigningKey;
use inari_gateway::audit::AuditContext;
use inari_gateway::identity::ActorId;
use inari_gateway::onboarding::InvitationCode;
use inari_gateway::protocol::{
    AgentPublication, DispatchEncryptionKey, DispatchKem, GatewaySnapshot,
};
use inari_gateway::{
    AgentEnrollmentRecord, GatewayError, GatewayRepository, GatewayResult, InvitationAttemptLimit,
    PreparedEnrollment,
};
use inari_migration::{Migrator, MigratorTrait};
use jsonwebtoken::jwk::{Jwk, ThumbprintHash};
use sea_orm::DatabaseConnection;
use serde_json::json;
use sha2::{Digest, Sha256};
use sqlx::PgPool;

const LIMIT: InvitationAttemptLimit =
    InvitationAttemptLimit { window: Duration::from_secs(60), max_failures: 3 };

pub(super) fn jwk(seed: u8, state: bool) -> Jwk {
    let public = SigningKey::from_bytes(&[seed; 32])
        .verifying_key()
        .to_bytes();
    serde_json::from_value(json!({
        "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig",
        "kid": if state { format!("agent_state_{:x}", Sha256::digest(public)) } else { format!("identity_{seed}") },
        "x": URL_SAFE_NO_PAD.encode(public),
    })).unwrap()
}

pub(super) fn enrollment(agent_id: &str, identity: Jwk, state: Jwk) -> AgentEnrollmentRecord {
    AgentEnrollmentRecord {
        agent_id: agent_id.parse().unwrap(),
        organization_id: "org_keys".parse().unwrap(),
        site_id: "site_keys".parse().unwrap(),
        key_id: identity.common.key_id.clone().unwrap(),
        jwk_thumbprint: identity.thumbprint(ThumbprintHash::SHA256),
        public_jwk: identity,
        dispatch_key: DispatchEncryptionKey {
            key_id: "dispatch_test".into(),
            kem: DispatchKem::DhkemX25519HkdfSha256,
            public_key_base64url: URL_SAFE_NO_PAD.encode([42; 32]),
        },
        state_signing_jwk: state,
        namespace: format!("inari/org_keys/site_keys/{agent_id}"),
        protocol_version: inari_gateway::protocol::ProtocolVersion::current(),
        controller_actions: vec![],
        csr_fingerprint: format!("csr_{agent_id}"),
    }
}

pub(super) fn snapshot() -> GatewaySnapshot {
    serde_json::from_value(json!({
        "generated_at": Utc::now(), "protocol": inari_gateway::protocol::ProtocolDescriptor::default(),
        "service": {}, "runtime": {"inventory": {"devices": []}}, "capabilities": {"transport": "https+zenoh"},
        "security": {"mode": "managed", "exposure": "private", "tls_required": true,
            "certificate_mode": "step_ca", "mutual_tls_mode": "required", "mutual_tls_enabled": true},
    })).unwrap()
}

pub(super) fn operator() -> AuditContext {
    AuditContext::new(ActorId::from_oidc_subject("test-operator"), None)
}

pub(super) async fn test_database() -> (PgPool, DatabaseConnection) {
    let pool = PgPool::connect(&std::env::var("INARI_TEST_DATABASE_URL").unwrap())
        .await
        .unwrap();
    let database = DatabaseConnection::from(pool.clone());
    (pool, database)
}

pub(super) async fn create_scope(pool: &PgPool) {
    sqlx::raw_sql("INSERT INTO organizations (organization_id, name) VALUES ('org_keys', 'Key tests');
        INSERT INTO sites (site_id, organization_id, name) VALUES ('site_keys', 'org_keys', 'Key tests');
        INSERT INTO sites (site_id, organization_id, name) VALUES ('site_other', 'org_keys', 'Other site');")
        .execute(pool).await.unwrap();
}

async fn invite(repository: &GatewayRepository, lifetime: chrono::Duration) -> InvitationCode {
    let code = InvitationCode::generate().unwrap();
    let now = Utc::now();
    repository
        .create_invitation(
            &code,
            &"org_keys".parse().unwrap(),
            &"site_keys".parse().unwrap(),
            None,
            now - chrono::Duration::hours(2)..now + lifetime,
            &operator(),
        )
        .await
        .unwrap();
    code
}

pub(super) async fn live_invitation(repository: &GatewayRepository) -> InvitationCode {
    invite(repository, chrono::Duration::hours(1)).await
}

/// Keeps the invitation ID and replaces the secret.
fn wrong_secret(code: &InvitationCode) -> InvitationCode {
    let normalized = code.normalized();
    let other = InvitationCode::generate()
        .unwrap()
        .normalized();
    let split = normalized
        .find(code.id().as_str())
        .unwrap()
        + code.id().as_str().len();
    format!("{}{}", &normalized[..split], &other[split..])
        .parse()
        .unwrap()
}

/// Enrolls with a `prepare` step that counts its calls, as the CA token
/// issuer would be called.
async fn enroll(
    repository: &GatewayRepository,
    code: &InvitationCode,
    record: AgentEnrollmentRecord,
    prepared: &Cell<usize>,
) -> GatewayResult<PreparedEnrollment<()>> {
    repository
        .enroll_agent(code, record, &snapshot(), LIMIT, || {
            prepared.set(prepared.get() + 1);
            Ok(())
        })
        .await
}

async fn invitation_state(pool: &PgPool, code: &InvitationCode) -> String {
    sqlx::query_scalar("SELECT state FROM invitations WHERE invitation_id = $1")
        .bind(code.id().as_str())
        .fetch_one(pool)
        .await
        .unwrap()
}

async fn count(pool: &PgPool, sql: &'static str) -> i64 {
    sqlx::query_scalar(sql)
        .fetch_one(pool)
        .await
        .unwrap()
}

async fn failed_attempts(pool: &PgPool, code: &InvitationCode) -> i64 {
    sqlx::query_scalar("SELECT count(*) FROM invitation_attempts WHERE invitation_id = $1")
        .bind(code.id().as_str())
        .fetch_one(pool)
        .await
        .unwrap()
}

async fn set_expiry(pool: &PgPool, code: &InvitationCode, expires_at: DateTime<Utc>) {
    sqlx::query("UPDATE invitations SET expires_at = $2 WHERE invitation_id = $1")
        .bind(code.id().as_str())
        .bind(expires_at)
        .execute(pool)
        .await
        .unwrap();
}

/// Waits until a PostgreSQL backend waits on a row lock, or until `task`
/// finishes without waiting. Returns whether a lock wait was seen.
async fn lock_wait<T>(pool: &PgPool, task: &tokio::task::JoinHandle<T>) -> bool {
    for _ in 0..100 {
        if task.is_finished() {
            return false;
        }
        let waiting: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM pg_stat_activity
            WHERE datname = current_database() AND wait_event_type = 'Lock'",
        )
        .fetch_one(pool)
        .await
        .unwrap();
        if waiting > 0 {
            return true;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    false
}

async fn enrolled_audits(pool: &PgPool, agent_id: &str) -> i64 {
    sqlx::query_scalar(
        "SELECT count(*) FROM audit_events WHERE action = 'agent.enrolled' AND resource_id = $1",
    )
    .bind(agent_id)
    .fetch_one(pool)
    .await
    .unwrap()
}

#[tokio::test]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn enrollment_migrations_retire_claims_and_retain_key_history() {
    let (pool, database) = test_database().await;
    Migrator::up(&database, Some(6))
        .await
        .unwrap();
    create_scope(&pool).await;
    let first = enrollment("agt_first", jwk(1, false), jwk(2, true));
    sqlx::query(
        "INSERT INTO agents (agent_id, organization_id, site_id, key_id, jwk_thumbprint,
        public_jwk, namespace, protocol_version, controller_actions, enrolled_at, last_enrolled_at, certificate_pem)
        VALUES ('agt_first', 'org_keys', 'site_keys', $1, $2, $3, $4, '1.0', '[]', now(), now(), 'obsolete certificate')",
    )
    .bind(&first.key_id)
    .bind(&first.jwk_thumbprint)
    .bind(serde_json::to_value(&first.public_jwk).unwrap())
    .bind(&first.namespace)
    .execute(&pool)
    .await
    .unwrap();
    let atomic_migration = Migrator::migrations()
        .iter()
        .position(|migration| migration.name() == "m20261003_223034_atomic_enrollment")
        .unwrap();
    let before_atomic_enrollment = u32::try_from(atomic_migration - 6).unwrap();
    Migrator::up(&database, Some(before_atomic_enrollment))
        .await
        .unwrap();
    let obsolete_certificate_column: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns \
         WHERE table_schema = 'public' AND table_name = 'agents' \
         AND column_name = 'certificate_pem')",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(!obsolete_certificate_column);
    let migrated: String =
        sqlx::query_scalar("SELECT purpose FROM agent_verification_keys WHERE key_id = $1")
            .bind(&first.key_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(migrated, "transport_identity");

    let claimed = InvitationCode::generate().unwrap();
    let historical = InvitationCode::generate().unwrap();
    for (code, state) in [(&claimed, "claimed"), (&historical, "enrolled")] {
        sqlx::query(
            "INSERT INTO invitations (invitation_id, organization_id, site_id, secret_digest,
            state, created_at, expires_at, claimed_at, enrolled_at, bound_agent_id, bound_key_id)
            VALUES ($1, 'org_keys', 'site_keys', $2, $3, now(), now() + interval '1 hour', now(),
                CASE WHEN $3 = 'enrolled' THEN now() END, 'agt_first', $4)",
        )
        .bind(code.id().as_str())
        .bind(code.secret_digest().to_vec())
        .bind(state)
        .bind(&first.key_id)
        .execute(&pool)
        .await
        .unwrap();
    }
    Migrator::up(&database, None)
        .await
        .unwrap();
    assert_eq!(invitation_state(&pool, &claimed).await, "failed");
    let failed_at: bool = sqlx::query_scalar(
        "SELECT failed_at IS NOT NULL AND last_error IS NOT NULL FROM invitations
        WHERE invitation_id = $1",
    )
    .bind(claimed.id().as_str())
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(failed_at);
    assert!(
        sqlx::query("UPDATE invitations SET state = 'claimed' WHERE invitation_id = $1")
            .bind(claimed.id().as_str())
            .execute(&pool)
            .await
            .is_err()
    );
    assert!(
        sqlx::query("UPDATE invitations SET enrollment_fingerprint = $2 WHERE invitation_id = $1")
            .bind(historical.id().as_str())
            .bind(vec![7u8; 31])
            .execute(&pool)
            .await
            .is_err()
    );

    let repository = GatewayRepository::new(database);
    let prepared = Cell::new(0);
    // Without a stored fingerprint, the original CSR is unknown.
    assert!(matches!(
        enroll(&repository, &historical, first.clone(), &prepared).await,
        Err(GatewayError::Conflict(_))
    ));
    assert!(matches!(
        enroll(&repository, &claimed, first.clone(), &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    assert_eq!(prepared.get(), 0);

    let first_code = live_invitation(&repository).await;
    enroll(&repository, &first_code, first.clone(), &prepared)
        .await
        .unwrap();
    let replay_code = live_invitation(&repository).await;
    enroll(&repository, &replay_code, first.clone(), &prepared)
        .await
        .unwrap();
    assert_eq!(count(&pool, "SELECT count(*) FROM agent_verification_keys").await, 2);

    let mut rotated = first.clone();
    rotated.state_signing_jwk = jwk(3, true);
    let rotation_code = live_invitation(&repository).await;
    enroll(&repository, &rotation_code, rotated, &prepared)
        .await
        .unwrap();
    assert_eq!(count(&pool, "SELECT count(*) FROM agent_verification_keys").await, 3);
    let retired: bool = sqlx::query_scalar(
        "SELECT retired_at IS NOT NULL FROM agent_verification_keys WHERE key_id = $1",
    )
    .bind(
        first
            .state_signing_jwk
            .common
            .key_id
            .as_ref()
            .unwrap(),
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(retired);
    assert_eq!(
        count(&pool, "SELECT count(*) FROM agent_verification_keys WHERE agent_id = 'agt_first' AND purpose = 'agent_state' AND retired_at IS NULL").await,
        1
    );

    // The rotation superseded the earlier invitation, so its exact retry would
    // return credentials for a retired key.
    assert!(matches!(
        enroll(&repository, &replay_code, first.clone(), &prepared).await,
        Err(GatewayError::Conflict(_))
    ));
    let reactivate_code = live_invitation(&repository).await;
    assert!(matches!(
        enroll(&repository, &reactivate_code, first.clone(), &prepared).await,
        Err(GatewayError::Conflict(_))
    ));
    assert_eq!(invitation_state(&pool, &reactivate_code).await, "created");

    for rejected in [
        enrollment("agt_other", jwk(4, false), first.state_signing_jwk.clone()),
        enrollment("agt_other", first.state_signing_jwk.clone(), jwk(5, true)),
    ] {
        let code = live_invitation(&repository).await;
        assert!(matches!(
            enroll(&repository, &code, rejected, &prepared).await,
            Err(GatewayError::Conflict(_))
        ));
        assert_eq!(invitation_state(&pool, &code).await, "created");
        assert_eq!(
            count(&pool, "SELECT count(*) FROM agents WHERE agent_id = 'agt_other'").await,
            0
        );
    }
    assert_eq!(count(&pool, "SELECT count(*) FROM agent_verification_keys").await, 3);
}

// `prepare` is synchronous. The replay race blocks it inside `block_in_place`,
// which needs the multi-thread runtime.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn enrollment_consumes_invitations_atomically() {
    let (pool, database) = test_database().await;
    Migrator::up(&database, None)
        .await
        .unwrap();
    create_scope(&pool).await;
    let repository = GatewayRepository::new(database);

    // One commit stores the Agent, both keys, the invitation, and one audit event.
    let prepared = Cell::new(0);
    let code = live_invitation(&repository).await;
    let committed = enrollment("agt_commit", jwk(10, false), jwk(11, true));
    let first = enroll(&repository, &code, committed.clone(), &prepared)
        .await
        .unwrap();
    assert_eq!(prepared.get(), 1);
    assert_eq!(invitation_state(&pool, &code).await, "enrolled");
    let fingerprint_bytes: i32 = sqlx::query_scalar(
        "SELECT octet_length(enrollment_fingerprint) FROM invitations WHERE invitation_id = $1",
    )
    .bind(code.id().as_str())
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(fingerprint_bytes, 32);
    assert_eq!(
        count(&pool, "SELECT count(*) FROM agent_verification_keys WHERE agent_id = 'agt_commit'")
            .await,
        2
    );
    assert_eq!(enrolled_audits(&pool, "agt_commit").await, 1);

    // An exact retry mints a fresh token and writes nothing.
    let retry = committed.clone();
    let replayed = enroll(&repository, &code, retry, &prepared)
        .await
        .unwrap();
    assert_eq!(prepared.get(), 2);
    assert_eq!(replayed.enrolled_at, first.enrolled_at);
    assert_eq!(enrolled_audits(&pool, "agt_commit").await, 1);
    assert_eq!(
        count(&pool, "SELECT count(*) FROM agent_verification_keys WHERE agent_id = 'agt_commit'")
            .await,
        2
    );

    // Changed facts from the bound Agent conflict. Another Site is forbidden.
    let mut changed_csr = committed.clone();
    changed_csr.csr_fingerprint = "csr_changed".into();
    let mut changed_dispatch = committed.clone();
    changed_dispatch
        .dispatch_key
        .public_key_base64url = URL_SAFE_NO_PAD.encode([43; 32]);
    let mut changed_state_key = committed.clone();
    changed_state_key.state_signing_jwk = jwk(12, true);
    let mut changed_actions = committed.clone();
    changed_actions.controller_actions = vec!["print".into()];
    for changed in [changed_csr, changed_dispatch, changed_state_key, changed_actions] {
        assert!(matches!(
            enroll(&repository, &code, changed, &prepared).await,
            Err(GatewayError::Conflict(_))
        ));
    }
    let mut changed_site = committed.clone();
    changed_site.site_id = "site_other".parse().unwrap();
    assert!(matches!(
        enroll(&repository, &code, changed_site, &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    let other_agent = enrollment("agt_intruder", jwk(13, false), jwk(14, true));
    assert!(matches!(
        enroll(&repository, &code, other_agent, &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    assert_eq!(prepared.get(), 2);

    let reconnect = live_invitation(&repository).await;
    let online = enrollment("agt_reconnected", jwk(15, false), jwk(16, true));
    enroll(&repository, &reconnect, online.clone(), &prepared)
        .await
        .unwrap();
    repository
        .record_publication(
            online.agent_id.as_str(),
            "status/first",
            &AgentPublication::StatusSnapshot {
                message_id: "status_first".into(),
                snapshot: Box::new(snapshot()),
            },
            Utc::now(),
        )
        .await
        .unwrap();
    assert_eq!(invitation_state(&pool, &reconnect).await, "online");
    let current = live_invitation(&repository).await;
    enroll(&repository, &current, online.clone(), &prepared)
        .await
        .unwrap();
    let old_online_at: DateTime<Utc> =
        sqlx::query_scalar("SELECT online_at FROM invitations WHERE invitation_id = $1")
            .bind(reconnect.id().as_str())
            .fetch_one(&pool)
            .await
            .unwrap();
    repository
        .record_publication(
            online.agent_id.as_str(),
            "status/current",
            &AgentPublication::StatusSnapshot {
                message_id: "status_current".into(),
                snapshot: Box::new(snapshot()),
            },
            Utc::now(),
        )
        .await
        .unwrap();
    assert_eq!(invitation_state(&pool, &current).await, "online");
    let retained_online_at: DateTime<Utc> =
        sqlx::query_scalar("SELECT online_at FROM invitations WHERE invitation_id = $1")
            .bind(reconnect.id().as_str())
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(retained_online_at, old_online_at);
    let before_replay = prepared.get();
    assert!(matches!(
        enroll(&repository, &current, online, &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    assert_eq!(prepared.get(), before_replay);

    // An issuer failure rolls back before the invitation is consumed.
    let code = live_invitation(&repository).await;
    let issuer_failure = enrollment("agt_issuer", jwk(20, false), jwk(21, true));
    assert!(matches!(
        repository
            .enroll_agent(&code, issuer_failure.clone(), &snapshot(), LIMIT, || {
                Err::<(), _>(GatewayError::Unavailable("issuer is down".into()))
            })
            .await,
        Err(GatewayError::Unavailable(_))
    ));
    assert_eq!(invitation_state(&pool, &code).await, "created");
    assert_eq!(count(&pool, "SELECT count(*) FROM agents WHERE agent_id = 'agt_issuer'").await, 0);
    assert_eq!(enrolled_audits(&pool, "agt_issuer").await, 0);
    enroll(&repository, &code, issuer_failure, &prepared)
        .await
        .unwrap();

    // Wrong secrets never reach the issuer and still count after Forbidden.
    let prepared = Cell::new(0);
    let code = live_invitation(&repository).await;
    let throttled = enrollment("agt_throttled", jwk(30, false), jwk(31, true));
    for _ in 0..LIMIT.max_failures {
        assert!(matches!(
            enroll(&repository, &wrong_secret(&code), throttled.clone(), &prepared).await,
            Err(GatewayError::Forbidden(_))
        ));
    }
    assert_eq!(failed_attempts(&pool, &code).await, 3);
    assert!(matches!(
        enroll(&repository, &code, throttled.clone(), &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    assert_eq!(prepared.get(), 0);
    assert_eq!(invitation_state(&pool, &code).await, "created");
    sqlx::query(
        "UPDATE invitation_attempts SET attempted_at = attempted_at - interval '61 seconds'
        WHERE invitation_id = $1",
    )
    .bind(code.id().as_str())
    .execute(&pool)
    .await
    .unwrap();
    enroll(&repository, &code, throttled, &prepared)
        .await
        .unwrap();
    assert_eq!(prepared.get(), 1);
    assert_eq!(failed_attempts(&pool, &code).await, 0);

    // A key conflict after token issuance discards the token and the claim.
    let code = live_invitation(&repository).await;
    let stolen_key =
        enrollment("agt_conflict", jwk(40, false), committed.state_signing_jwk.clone());
    assert!(matches!(
        enroll(&repository, &code, stolen_key, &prepared).await,
        Err(GatewayError::Conflict(_))
    ));
    assert_eq!(invitation_state(&pool, &code).await, "created");
    assert_eq!(
        count(&pool, "SELECT count(*) FROM agents WHERE agent_id = 'agt_conflict'").await,
        0
    );
    assert_eq!(enrolled_audits(&pool, "agt_conflict").await, 0);

    // The row lock serializes concurrent requests for one invitation.
    let code = live_invitation(&repository).await;
    let same = enrollment("agt_same", jwk(50, false), jwk(51, true));
    let (left, right) = tokio::join!(
        enroll(&repository, &code, same.clone(), &prepared),
        enroll(&repository, &code, same, &prepared),
    );
    assert_eq!(left.unwrap().enrolled_at, right.unwrap().enrolled_at);
    assert_eq!(enrolled_audits(&pool, "agt_same").await, 1);

    let code = live_invitation(&repository).await;
    let (left, right) = tokio::join!(
        enroll(
            &repository,
            &code,
            enrollment("agt_left", jwk(60, false), jwk(61, true)),
            &prepared
        ),
        enroll(
            &repository,
            &code,
            enrollment("agt_right", jwk(62, false), jwk(63, true)),
            &prepared
        ),
    );
    assert_eq!(usize::from(left.is_ok()) + usize::from(right.is_ok()), 1);
    assert!(matches!(left.err().or(right.err()), Some(GatewayError::Forbidden(_))));
    assert_eq!(
        count(&pool, "SELECT count(*) FROM agents WHERE agent_id IN ('agt_left', 'agt_right')")
            .await,
        1
    );

    // Expiry and revocation reject before the issuer runs.
    let prepared = Cell::new(0);
    let expired = invite(&repository, -chrono::Duration::hours(1)).await;
    let late = enrollment("agt_late", jwk(70, false), jwk(71, true));
    assert!(matches!(
        enroll(&repository, &expired, late.clone(), &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    let revoked = live_invitation(&repository).await;
    repository
        .revoke_invitation(
            revoked.id().as_str(),
            Utc::now(),
            &"org_keys".parse().unwrap(),
            &operator(),
        )
        .await
        .unwrap();
    assert!(matches!(
        enroll(&repository, &revoked, late, &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    assert_eq!(prepared.get(), 0);
    assert_eq!(count(&pool, "SELECT count(*) FROM agents WHERE agent_id = 'agt_late'").await, 0);

    let code = live_invitation(&repository).await;
    let enrolled = enrollment("agt_revoked", jwk(80, false), jwk(81, true));
    enroll(&repository, &code, enrolled.clone(), &prepared)
        .await
        .unwrap();
    set_expiry(&pool, &code, Utc::now() - chrono::Duration::seconds(1)).await;
    assert!(matches!(
        enroll(&repository, &code, enrolled.clone(), &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    set_expiry(&pool, &code, Utc::now() + chrono::Duration::hours(1)).await;
    enroll(&repository, &code, enrolled.clone(), &prepared)
        .await
        .unwrap();
    repository
        .revoke_invitation(
            code.id().as_str(),
            Utc::now(),
            &"org_keys".parse().unwrap(),
            &operator(),
        )
        .await
        .unwrap();
    assert!(matches!(
        enroll(&repository, &code, enrolled, &prepared).await,
        Err(GatewayError::Forbidden(_))
    ));
    assert_eq!(prepared.get(), 2);
    assert_eq!(enrolled_audits(&pool, "agt_revoked").await, 1);

    // A replay holds the Agent row while it prepares. A rotation through
    // another invitation waits, so the replay cannot return retired keys.
    let code = live_invitation(&repository).await;
    let replaced = enrollment("agt_replaced", jwk(90, false), jwk(91, true));
    enroll(&repository, &code, replaced.clone(), &prepared)
        .await
        .unwrap();
    let (entered, entered_signal) = tokio::sync::oneshot::channel();
    let (release, release_signal) = std::sync::mpsc::channel::<()>();
    let replay = tokio::spawn({
        let repository = repository.clone();
        let code = code.clone();
        let record = replaced.clone();
        async move {
            repository
                .enroll_agent(&code, record, &snapshot(), LIMIT, move || {
                    let _ = entered.send(());
                    // Hands this worker's runtime duties to another thread
                    // while the replay holds its locks.
                    tokio::task::block_in_place(|| release_signal.recv())
                        .map_err(|_| GatewayError::Unavailable("test released early".into()))
                })
                .await
        }
    });
    entered_signal.await.unwrap();
    let rotation_code = live_invitation(&repository).await;
    let mut rotation = replaced.clone();
    rotation.state_signing_jwk = jwk(92, true);
    let rotation = tokio::spawn({
        let repository = repository.clone();
        async move {
            repository
                .enroll_agent(&rotation_code, rotation, &snapshot(), LIMIT, || Ok(()))
                .await
        }
    });
    assert!(lock_wait(&pool, &rotation).await, "the rotation did not wait for the replay");
    release.send(()).unwrap();
    replay.await.unwrap().unwrap();
    rotation.await.unwrap().unwrap();
    assert!(matches!(
        enroll(&repository, &code, replaced, &prepared).await,
        Err(GatewayError::Conflict(_))
    ));

    // An invitation that expires while a request waits for its lock cannot
    // mint credentials when the request gets the lock.
    let code = live_invitation(&repository).await;
    let mut holder = pool.begin().await.unwrap();
    sqlx::query("SELECT 1 FROM invitations WHERE invitation_id = $1 FOR UPDATE")
        .bind(code.id().as_str())
        .execute(&mut *holder)
        .await
        .unwrap();
    let issued = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let blocked = tokio::spawn({
        let repository = repository.clone();
        let code = code.clone();
        let issued = issued.clone();
        let record = enrollment("agt_blocked", jwk(95, false), jwk(96, true));
        async move {
            repository
                .enroll_agent(&code, record, &snapshot(), LIMIT, move || {
                    issued.store(true, std::sync::atomic::Ordering::SeqCst);
                    Ok(())
                })
                .await
        }
    });
    assert!(lock_wait(&pool, &blocked).await, "the enrollment did not wait for the lock");
    sqlx::query("UPDATE invitations SET expires_at = $2 WHERE invitation_id = $1")
        .bind(code.id().as_str())
        .bind(Utc::now())
        .execute(&mut *holder)
        .await
        .unwrap();
    holder.commit().await.unwrap();
    assert!(matches!(blocked.await.unwrap(), Err(GatewayError::Forbidden(_))));
    assert!(!issued.load(std::sync::atomic::Ordering::SeqCst));
    assert_eq!(count(&pool, "SELECT count(*) FROM agents WHERE agent_id = 'agt_blocked'").await, 0);

    let code = live_invitation(&repository).await;
    let record = enrollment("agt_replay_expiry", jwk(97, false), jwk(98, true));
    enroll(&repository, &code, record.clone(), &prepared)
        .await
        .unwrap();
    let expires_at = Utc::now() + chrono::Duration::seconds(2);
    set_expiry(&pool, &code, expires_at).await;
    let mut holder = pool.begin().await.unwrap();
    sqlx::query("SELECT 1 FROM agents WHERE agent_id = 'agt_replay_expiry' FOR UPDATE")
        .execute(&mut *holder)
        .await
        .unwrap();
    let issued = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let replay = tokio::spawn({
        let repository = repository.clone();
        let issued = issued.clone();
        async move {
            repository
                .enroll_agent(&code, record, &snapshot(), LIMIT, move || {
                    issued.store(true, std::sync::atomic::Ordering::SeqCst);
                    Ok(())
                })
                .await
        }
    });
    assert!(lock_wait(&pool, &replay).await);
    tokio::time::sleep(
        (expires_at - Utc::now())
            .to_std()
            .unwrap_or_default()
            + Duration::from_millis(50),
    )
    .await;
    holder.commit().await.unwrap();
    assert!(matches!(replay.await.unwrap(), Err(GatewayError::Forbidden(_))));
    assert!(!issued.load(std::sync::atomic::Ordering::SeqCst));
    assert_eq!(enrolled_audits(&pool, "agt_replay_expiry").await, 1);
}
