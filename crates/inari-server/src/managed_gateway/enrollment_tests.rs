use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::Utc;
use ed25519_dalek::SigningKey;
use inari_gateway::protocol::{DispatchEncryptionKey, DispatchKem, GatewaySnapshot};
use inari_gateway::{AgentEnrollmentRecord, GatewayRepository};
use inari_migration::{Migrator, MigratorTrait};
use jsonwebtoken::jwk::{Jwk, ThumbprintHash};
use sea_orm::DatabaseConnection;
use serde_json::json;
use sha2::{Digest, Sha256};
use sqlx::PgPool;

fn jwk(seed: u8, state: bool) -> Jwk {
    let public = SigningKey::from_bytes(&[seed; 32])
        .verifying_key()
        .to_bytes();
    serde_json::from_value(json!({
        "kty": "OKP", "crv": "Ed25519", "alg": "EdDSA", "use": "sig",
        "kid": if state { format!("agent_state_{:x}", Sha256::digest(public)) } else { format!("identity_{seed}") },
        "x": URL_SAFE_NO_PAD.encode(public),
    })).unwrap()
}

fn enrollment(agent_id: &str, identity: Jwk, state: Jwk) -> AgentEnrollmentRecord {
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
        certificate_pem: None,
        namespace: format!("inari/org_keys/site_keys/{agent_id}"),
        protocol_version: inari_gateway::protocol::ProtocolVersion::current(),
        controller_actions: vec![],
        enrolled_at: Utc::now(),
    }
}

fn snapshot() -> GatewaySnapshot {
    serde_json::from_value(json!({
        "generated_at": Utc::now(), "protocol": inari_gateway::protocol::ProtocolDescriptor::default(),
        "service": {}, "runtime": {}, "capabilities": {"transport": "https+zenoh"},
        "security": {"mode": "managed", "exposure": "private", "tls_required": true,
            "certificate_mode": "step_ca", "mutual_tls_mode": "required", "mutual_tls_enabled": true},
    })).unwrap()
}

async fn invitation(pool: &PgPool, id: &str, record: &AgentEnrollmentRecord) {
    sqlx::query("INSERT INTO invitations (invitation_id, organization_id, site_id, secret_digest,
        state, created_at, expires_at, bound_agent_id, bound_key_id)
        VALUES ($1, 'org_keys', 'site_keys', $2, 'claimed', now(), now() + interval '1 hour', $3, $4)")
        .bind(id).bind(vec![1u8; 32]).bind(record.agent_id.as_str()).bind(&record.key_id)
        .execute(pool).await.unwrap();
}

#[tokio::test]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn enrollment_retains_key_history_and_rejects_owner_or_purpose_reuse() {
    let pool = PgPool::connect(&std::env::var("INARI_TEST_DATABASE_URL").unwrap())
        .await
        .unwrap();
    let database = DatabaseConnection::from(pool.clone());
    Migrator::up(&database, Some(6))
        .await
        .unwrap();
    sqlx::raw_sql("INSERT INTO organizations (organization_id, name) VALUES ('org_keys', 'Key tests');
        INSERT INTO sites (site_id, organization_id, name) VALUES ('site_keys', 'org_keys', 'Key tests');")
        .execute(&pool).await.unwrap();
    let first = enrollment("agt_first", jwk(1, false), jwk(2, true));
    sqlx::query(
        "INSERT INTO agents (agent_id, organization_id, site_id, key_id, jwk_thumbprint,
        public_jwk, namespace, protocol_version, controller_actions, enrolled_at, last_enrolled_at)
        VALUES ('agt_first', 'org_keys', 'site_keys', $1, $2, $3, $4, '1.0', '[]', now(), now())",
    )
    .bind(&first.key_id)
    .bind(&first.jwk_thumbprint)
    .bind(serde_json::to_value(&first.public_jwk).unwrap())
    .bind(&first.namespace)
    .execute(&pool)
    .await
    .unwrap();
    Migrator::up(&database, None)
        .await
        .unwrap();
    let migrated: String =
        sqlx::query_scalar("SELECT purpose FROM agent_verification_keys WHERE key_id = $1")
            .bind(&first.key_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(migrated, "transport_identity");

    let repository = GatewayRepository::new(database);
    invitation(&pool, "inv_first", &first).await;
    repository
        .enroll_agent(first.clone(), "inv_first", &snapshot())
        .await
        .unwrap();
    invitation(&pool, "inv_replay", &first).await;
    repository
        .enroll_agent(first.clone(), "inv_replay", &snapshot())
        .await
        .unwrap();
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM agent_verification_keys")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 2);

    let mut rotated = first.clone();
    rotated.state_signing_jwk = jwk(3, true);
    rotated.enrolled_at = Utc::now();
    invitation(&pool, "inv_rotation", &rotated).await;
    repository
        .enroll_agent(rotated, "inv_rotation", &snapshot())
        .await
        .unwrap();
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM agent_verification_keys")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 3);

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
    let active: i64 = sqlx::query_scalar("SELECT count(*) FROM agent_verification_keys WHERE agent_id = 'agt_first' AND purpose = 'agent_state' AND retired_at IS NULL")
        .fetch_one(&pool).await.unwrap();
    assert_eq!(active, 1);
    invitation(&pool, "inv_reactivate", &first).await;
    assert!(
        repository
            .enroll_agent(first.clone(), "inv_reactivate", &snapshot())
            .await
            .is_err()
    );

    for (name, rejected) in [
        ("owner", enrollment("agt_other", jwk(4, false), first.state_signing_jwk.clone())),
        ("purpose", enrollment("agt_other", first.state_signing_jwk.clone(), jwk(5, true))),
    ] {
        let invitation_id = format!("inv_{name}");
        invitation(&pool, &invitation_id, &rejected).await;
        assert!(
            repository
                .enroll_agent(rejected, &invitation_id, &snapshot())
                .await
                .is_err()
        );
        let state: String =
            sqlx::query_scalar("SELECT state FROM invitations WHERE invitation_id = $1")
                .bind(&invitation_id)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(state, "claimed");
        let count: i64 =
            sqlx::query_scalar("SELECT count(*) FROM agents WHERE agent_id = 'agt_other'")
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(count, 0);
    }
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM agent_verification_keys")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 3);
}
