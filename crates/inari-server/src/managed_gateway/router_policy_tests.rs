use std::sync::Arc;
use std::sync::atomic::{AtomicU8, Ordering};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{TimeDelta, Utc};
use ed25519_dalek::SigningKey;
use futures_util::future::BoxFuture;
use inari_gateway::GatewayRepository;
use inari_gateway::audit::AuditContext;
use inari_gateway::identity::ActorId;
use inari_migration::{Migrator, MigratorTrait};
use inari_router::SignedPolicy;
use inari_router::supervisor::RouterStatus;
use sea_orm::DatabaseConnection;
use sha2::{Digest, Sha256};
use sqlx::PgPool;

use super::{RouterAdmissionController, RouterManagement};
use crate::config::{RouterManagementConfig, RouterPolicyConfig};
use crate::error::{AppError, AppResult};

#[derive(Default)]
struct TestManagement {
    failure: AtomicU8,
    pool: Option<PgPool>,
}

impl RouterManagement for TestManagement {
    fn apply<'a>(
        &'a self,
        router: &'a RouterManagementConfig,
        policy: &'a SignedPolicy,
    ) -> BoxFuture<'a, AppResult<RouterStatus>> {
        Box::pin(async move {
            let verified = policy
                .verify(&SigningKey::from_bytes(&[71; 32]).verifying_key(), "test_fleet")
                .unwrap();
            let failure = if router.router_id == "router_two" {
                self.failure.load(Ordering::SeqCst)
            } else {
                0
            };
            if failure == 1 {
                return Err(AppError::service_unavailable("Router is unavailable."));
            }
            if failure == 5 {
                let pool = self.pool.as_ref().unwrap();
                tokio::time::timeout(std::time::Duration::from_secs(1),
                    sqlx::query("UPDATE agent_verification_keys SET retired_at = now() WHERE purpose = 'agent_state' AND retired_at IS NULL")
                        .execute(pool)).await.unwrap().unwrap();
            }
            Ok(RouterStatus {
                generation: Some(policy.policy.generation + u64::from(failure == 2)),
                digest: Some(if failure == 3 { "0".repeat(64) } else { verified.digest().into() }),
                expires_at: Some(policy.policy.expires_at),
                ready: failure != 4,
                message: "Test Router acknowledgment.".into(),
            })
        })
    }
}

pub(super) fn config() -> RouterPolicyConfig {
    RouterPolicyConfig {
        fleet_id: "test_fleet".into(),
        routers: ["router_one", "router_two"]
            .map(|router_id| RouterManagementConfig {
                router_id: router_id.into(),
                address: format!("https://{router_id}.example/")
                    .parse()
                    .unwrap(),
            })
            .into(),
        trusted_peer_common_names: vec!["controller_test".into()],
        signing_key_file: "/test/policy-key".into(),
        management_ca_file: "/test/management-ca".into(),
        management_certificate_file: "/test/management-cert".into(),
        management_private_key_file: "/test/management-key".into(),
    }
}

pub(super) fn admission_controller(
    repository: GatewayRepository,
    organization: &str,
    prefix: &str,
) -> Arc<RouterAdmissionController> {
    Arc::new(
        RouterAdmissionController::new(
            repository,
            organization.into(),
            config(),
            prefix.into(),
            SigningKey::from_bytes(&[71; 32]),
            Arc::new(TestManagement::default()),
        )
        .unwrap(),
    )
}

#[tokio::test]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn router_policy_generations_and_acknowledgments_are_transactional() {
    let pool = PgPool::connect(&std::env::var("INARI_TEST_DATABASE_URL").unwrap())
        .await
        .unwrap();
    let database = DatabaseConnection::from(pool.clone());
    Migrator::up(&database, None)
        .await
        .unwrap();
    sqlx::query("INSERT INTO organizations (organization_id, name) VALUES ('org_router_test', 'Router test')")
        .execute(&pool).await.unwrap();
    let repository = GatewayRepository::new(database);
    let management = Arc::new(TestManagement { pool: Some(pool.clone()), ..Default::default() });
    let controllers = (0..12)
        .map(|_| {
            RouterAdmissionController::new(
                repository.clone(),
                "org_router_test".into(),
                config(),
                "iot/v1/agents".into(),
                SigningKey::from_bytes(&[71; 32]),
                management.clone(),
            )
            .unwrap()
        })
        .collect::<Vec<_>>();
    let policies = futures_util::future::join_all(
        controllers
            .iter()
            .map(|controller| controller.reconcile()),
    )
    .await;
    assert!(policies.iter().all(Result::is_ok));
    let generation: i64 = sqlx::query_scalar(
        "SELECT generation FROM router_policies WHERE organization_id = 'org_router_test'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(generation, 1, "replicas reuse one exact signed generation");
    for failure in 1..=4 {
        management
            .failure
            .store(failure, Ordering::SeqCst);
        assert!(
            controllers[0]
                .reconcile()
                .await
                .is_err(),
            "every Router must acknowledge, failure {failure}"
        );
    }
    management
        .failure
        .store(0, Ordering::SeqCst);
    sqlx::query(
        "UPDATE router_policies SET expires_at = $1 WHERE organization_id = 'org_router_test'",
    )
    .bind(Utc::now() + TimeDelta::seconds(5))
    .execute(&pool)
    .await
    .unwrap();
    let policies = futures_util::future::join_all(
        controllers
            .iter()
            .map(|controller| controller.reconcile()),
    )
    .await;
    assert!(policies.iter().all(Result::is_ok));
    let generation: i64 = sqlx::query_scalar(
        "SELECT generation FROM router_policies WHERE organization_id = 'org_router_test'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(generation, 2, "refresh allocates one new generation across replicas");
    assert!(
        controllers[0]
            .admit("agt_000000000000000000000099")
            .await
            .is_err()
    );
    let public = SigningKey::from_bytes(&[11; 32])
        .verifying_key()
        .to_bytes();
    let agent_id = format!("agt_{}", hex::encode(&Sha256::digest(public)[..12]));
    sqlx::query("INSERT INTO sites (site_id, organization_id, name) VALUES ('site_router_test', 'org_router_test', 'Router site')")
        .execute(&pool).await.unwrap();
    sqlx::query("INSERT INTO agents (agent_id, organization_id, site_id, key_id, jwk_thumbprint, public_jwk,
        dispatch_key, namespace, protocol_version, controller_actions, enrolled_at, last_enrolled_at)
        VALUES ($1, 'org_router_test', 'site_router_test', 'identity_router_test', 'router-transport', $2,
        $3, $4, $5, '[]', now(), now())")
        .bind(&agent_id).bind(serde_json::json!({"kty":"OKP", "crv":"Ed25519", "kid":"identity_router_test", "x":URL_SAFE_NO_PAD.encode(public)}))
        .bind(serde_json::json!({"key_id":"dispatch_router_test", "kem":"dhkem_x25519_hkdf_sha256", "public_key_base64url":URL_SAFE_NO_PAD.encode([7;32])}))
        .bind(format!("iot/v1/agents/{agent_id}")).bind(inari_gateway::protocol::ProtocolVersion::current().to_string())
        .execute(&pool).await.unwrap();
    for (purpose, key_id, seed) in [
        ("transport_identity", "identity_router_test", 11),
        ("agent_state", "state_router_test", 12),
    ] {
        let public = SigningKey::from_bytes(&[seed; 32])
            .verifying_key()
            .to_bytes();
        sqlx::query("INSERT INTO agent_verification_keys (key_id, agent_id, purpose, jwk_thumbprint, public_jwk, registered_at)
            VALUES ($1, $2, $3, $4, $5, now())")
            .bind(key_id).bind(&agent_id).bind(purpose).bind(format!("router-key-{seed}"))
            .bind(serde_json::json!({"kty":"OKP", "crv":"Ed25519", "kid":key_id, "x":URL_SAFE_NO_PAD.encode(public)}))
            .execute(&pool).await.unwrap();
    }
    let admitted = controllers[0]
        .admit(&agent_id)
        .await
        .unwrap();
    repository
        .require_router_admission(&admitted, &agent_id)
        .await
        .unwrap();
    sqlx::query("UPDATE agents SET namespace = namespace WHERE agent_id = $1")
        .bind(&agent_id)
        .execute(&pool)
        .await
        .unwrap();
    assert!(
        repository
            .require_router_admission(&admitted, &agent_id)
            .await
            .is_err(),
        "an Agent change invalidates the acknowledgment in the same transaction"
    );
    let admitted = controllers[0]
        .admit(&agent_id)
        .await
        .unwrap();
    management
        .failure
        .store(5, Ordering::SeqCst);
    assert!(
        controllers[0]
            .reconcile()
            .await
            .is_err(),
        "authority changed during management HTTP"
    );
    assert!(
        repository
            .require_router_admission(&admitted, &agent_id)
            .await
            .is_err()
    );
    let acknowledged: bool = sqlx::query_scalar("SELECT acknowledged_at IS NOT NULL FROM router_policies WHERE organization_id = 'org_router_test'")
        .fetch_one(&pool).await.unwrap();
    assert!(!acknowledged, "an old signed policy cannot acknowledge a changed authority");
    management
        .failure
        .store(0, Ordering::SeqCst);
    let operator = AuditContext::new(ActorId::from_oidc_subject("retirement-test"), None);
    for _ in 0..2 {
        repository
            .retire_agent_credentials(
                &"org_router_test".parse().unwrap(),
                &agent_id.parse().unwrap(),
                &operator,
            )
            .await
            .unwrap();
    }
    assert!(
        controllers[0]
            .admit(&agent_id)
            .await
            .is_err(),
        "retired credentials cannot regain admission"
    );
    let audits: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM audit_events WHERE action = 'agent.credentials_retired'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(audits, 1, "credential retirement is idempotent");
    let retired: i64 = sqlx::query_scalar("SELECT count(*) FROM agent_verification_keys WHERE agent_id = $1 AND retired_at IS NOT NULL")
        .bind(&agent_id).fetch_one(&pool).await.unwrap();
    assert_eq!(retired, 2);
}
