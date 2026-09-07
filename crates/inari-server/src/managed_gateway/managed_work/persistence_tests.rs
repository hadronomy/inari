use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{TimeDelta, Utc};
use ed25519_dalek::pkcs8::EncodePrivateKey;
use ed25519_dalek::{Signer, SigningKey};
use futures_util::future::BoxFuture;
use hpke::kem::X25519HkdfSha256;
use hpke::{Kem, Serializable};
use inari_gateway::GatewayRepository;
use inari_gateway::protocol::{
    AgentPublication, AgentStateObservation, DispatchEncryptionKey, ManagedWorkPreflightRequest,
    ManagedWorkState, PrintJobState,
};
use inari_migration::{Migrator, MigratorTrait};
use sea_orm::DatabaseConnection;
use sha2::{Digest, Sha256};
use sqlx::PgPool;

use super::tests::submission;
use crate::config::{ManagedGatewayConfig, OrganizationConfig, ZenohConfig};
use crate::error::{AppError, AppResult};
use crate::managed_gateway::payload::{ManagedPayloadKeyWrapper, TransitWrappedKey};
use crate::managed_gateway::{
    ManagedDispatchSigner, ManagedGatewayController, ManagedPayloadProtector, ManagedWorkSecurity,
};
use crate::zenoh::ZenohSupervisor;

#[derive(Default)]
struct TestKeyWrapper {
    unavailable: AtomicBool,
    calls: AtomicUsize,
    key: Mutex<Vec<u8>>,
}

async fn prepare_submission(
    controller: &ManagedGatewayController,
    submission: &mut inari_gateway::protocol::ManagedWorkSubmission,
) {
    let preflight = controller
        .preflight_managed_work(ManagedWorkPreflightRequest {
            contract_major: 1,
            scope: submission.work.scope.clone(),
            device_id: submission.work.device_id.clone(),
            operation: submission.work.document.operation(),
            binding: submission.work.origin.binding.clone(),
        })
        .await
        .unwrap();
    submission.payload_fingerprint = hex::encode(
        inari_gateway::protocol::managed_work_fingerprint(
            &submission.work,
            preflight.expires_at.unwrap(),
        )
        .unwrap(),
    );
    submission.preflight_id = preflight.preflight_id.unwrap();
}

fn state_publication(observation: &AgentStateObservation, command_id: &str) -> AgentPublication {
    let key = SigningKey::from_bytes(&[42; 32]);
    let kid = format!("agent_state_{:x}", Sha256::digest(key.verifying_key().to_bytes()));
    let protected = URL_SAFE_NO_PAD.encode(
        serde_json_canonicalizer::to_vec(&serde_json::json!({
            "alg": "EdDSA", "kid": kid, "typ": "application/inari-agent-state+jws",
        }))
        .unwrap(),
    );
    let payload = URL_SAFE_NO_PAD.encode(serde_json_canonicalizer::to_vec(observation).unwrap());
    let input = format!("{protected}.{payload}");
    let compact =
        format!("{input}.{}", URL_SAFE_NO_PAD.encode(key.sign(input.as_bytes()).to_bytes()));
    let state = serde_json::to_value(observation.job.state).unwrap();
    serde_json::from_value(serde_json::json!({
        "type": "agent.runtime.event", "message_id": observation.envelope_id,
        "occurred_at": observation.observed_at, "command_id": command_id,
        "job_id": observation.job.print_job_id,
        "event": {
            "sequence": observation.durable_state_sequence, "resource_kind": "print_job",
            "resource_id": observation.job.print_job_id,
            "event_type": format!("print_job.{}", state.as_str().unwrap()),
            "occurred_at": observation.job.terminal_at.or(observation.job.started_at).unwrap_or(observation.job.accepted_at),
            "payload": {"state_envelope": compact},
        },
    })).unwrap()
}

#[tokio::test]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn signed_state_recovers_acceptance_and_preserves_terminal_evidence() {
    let pool = PgPool::connect(&std::env::var("INARI_TEST_DATABASE_URL").unwrap())
        .await
        .unwrap();
    let database = DatabaseConnection::from(pool.clone());
    Migrator::up(&database, None)
        .await
        .unwrap();
    seed_database(&pool).await;
    let repository = GatewayRepository::new(database);
    let directory = tempfile::tempdir().unwrap();
    let signing_key_file = directory
        .path()
        .join("dispatch-key.der");
    std::fs::write(
        &signing_key_file,
        SigningKey::from_bytes(&[7; 32])
            .to_pkcs8_der()
            .unwrap()
            .as_bytes(),
    )
    .unwrap();
    let mut config = ManagedGatewayConfig { enabled: true, ..Default::default() };
    config.dispatch.enabled = true;
    config.dispatch.signing_key_id = Some("dispatch_test".into());
    config.dispatch.signing_key_file = Some(signing_key_file);
    let signer = ManagedDispatchSigner::load(&config.dispatch, "controller_test")
        .await
        .unwrap();
    let (zenoh, _) = ZenohSupervisor::new(ZenohConfig::default());
    let controller = ManagedGatewayController::new(
        config,
        OrganizationConfig { id: "org_example".parse().unwrap(), ..Default::default() },
        ZenohConfig::default(),
        zenoh,
        Some(repository.clone()),
        None,
        Some(Arc::new(ManagedWorkSecurity::new(
            signer,
            ManagedPayloadProtector::new(Arc::new(TestKeyWrapper::default())),
        ))),
    );
    let mut submission = submission();
    prepare_submission(&controller, &mut submission).await;
    let receipt = controller
        .submit_managed_work(
            "mw_observation".parse().unwrap(),
            "idempotency_observation".into(),
            submission.clone(),
        )
        .await
        .unwrap();
    let work = controller
        .managed_work(&receipt.managed_work_id)
        .await
        .unwrap();
    let command_id: String = sqlx::query_scalar(
        "SELECT command_id FROM commands WHERE command ->> 'managed_work_id' = 'mw_observation'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../inari-gateway/tests/fixtures/agent-state-observation.json"
    ))
    .unwrap();
    let key = &fixture["public_jwk"];
    sqlx::query("INSERT INTO agent_verification_keys (key_id, agent_id, purpose, jwk_thumbprint, public_jwk, registered_at) VALUES ($1, 'agt_example', 'transport_identity', 'state-test', $2, now())")
        .bind(key["kid"].as_str().unwrap()).bind(key).execute(&pool).await.unwrap();
    let mut observation: AgentStateObservation =
        serde_json::from_value(fixture["claims"].clone()).unwrap();
    let now = Utc::now();
    observation.agent_id = work.scope.agent_id.clone();
    observation.observed_at = now;
    observation.issued_at = now;
    observation.payload_fingerprint = format!("sha256:{}", sqlx::query_scalar::<_, String>("SELECT encode(payload_fingerprint, 'hex') FROM managed_work WHERE managed_work_id = 'mw_observation'").fetch_one(&pool).await.unwrap());
    observation.job.managed_work_id = work.managed_work_id.clone();
    observation.job.print_intent_id = work.print_intent_id.clone();
    observation.job.device_id = work.device_id.clone();
    observation.job.accepted_at = now;
    observation.job.expires_at = work.expires_at;
    observation.job.origin.organization_id = work.scope.organization_id.clone();
    observation.job.origin.site_id = work.scope.site_id.clone();
    observation.job.origin.database = work.scope.database.clone();
    observation.job.origin.company_id = work.scope.company_id.clone();
    observation.job.origin.report_route = submission.work.origin.route;
    observation.job.origin.source_model = "sale.order".into();
    observation.job.origin.record_ids = vec!["42".into()];
    observation.job.origin.report_binding_id = submission
        .work
        .origin
        .binding
        .report_binding_id
        .clone();
    observation.job.origin.report_action = submission
        .work
        .origin
        .binding
        .report_action_id
        .clone();
    let accepted = observation.clone();
    observation.envelope_id = "ase_terminal".into();
    observation.envelope_sequence = 3;
    observation.durable_state_sequence = 3;
    observation.job.state = PrintJobState::OutcomeUnknown;
    observation.job.state_version = 3;
    observation.job.started_at = Some(now);
    observation.job.terminal_at = Some(now);
    observation.job.error_code = Some("output_unconfirmed".into());
    observation.job.message_key = Some("print.output_unconfirmed".into());
    let terminal = state_publication(&observation, &command_id);
    assert!(
        repository
            .record_publication("agt_example", "test", &terminal, now)
            .await
            .is_err(),
        "transport identity cannot sign Agent State"
    );
    sqlx::query("UPDATE agent_verification_keys SET purpose = 'agent_state' WHERE key_id = $1")
        .bind(key["kid"].as_str().unwrap())
        .execute(&pool)
        .await
        .unwrap();
    for field in ["site", "fingerprint", "device", "company"] {
        let mut wrong = observation.clone();
        wrong.envelope_id = format!("ase_wrong_{field}");
        match field {
            "site" => wrong.job.origin.site_id = "site_other".parse().unwrap(),
            "fingerprint" => wrong.payload_fingerprint = format!("sha256:{}", "a".repeat(64)),
            "device" => wrong.job.device_id = "dev_other".parse().unwrap(),
            _ => wrong.job.origin.company_id = "99".into(),
        }
        assert!(
            repository
                .record_publication(
                    "agt_example",
                    "test",
                    &state_publication(&wrong, &command_id),
                    now
                )
                .await
                .is_err()
        );
    }
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM managed_payloads")
            .fetch_one(&pool)
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM agent_state_observations")
            .fetch_one(&pool)
            .await
            .unwrap(),
        0
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM publications")
            .fetch_one(&pool)
            .await
            .unwrap(),
        0
    );
    sqlx::query("UPDATE managed_work SET state = 'recovery_uncertain' WHERE managed_work_id = 'mw_observation'")
        .execute(&pool).await.unwrap();
    let commit_key = "iot/v1/agents/agt_example/state/commit";
    let commit_payload = serde_json::to_vec(&terminal).unwrap();
    for key in [
        "iot/v1/agents/agt_unknown/state/commit",
        "iot/v1/agents/agt_example/extra/state/commit",
        "iot/v1/agents/*/state/commit",
    ] {
        assert!(
            controller
                .commit_state_publication(key, &commit_payload)
                .await
                .is_err()
        );
    }
    let (first, replay) = tokio::join!(
        controller.commit_state_publication(commit_key, &commit_payload),
        controller.commit_state_publication(commit_key, &commit_payload)
    );
    let first = first.unwrap();
    assert_eq!(first, replay.unwrap());
    assert_eq!(first.contract_major, 1);
    assert_eq!(first.message_id, observation.envelope_id);
    let envelope = serde_json::to_value(&terminal).unwrap()["event"]["payload"]["state_envelope"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(first.state_envelope_sha256, hex::encode(Sha256::digest(envelope.as_bytes())));
    let recovered = controller
        .managed_work(&receipt.managed_work_id)
        .await
        .unwrap();
    assert_eq!(recovered.state, ManagedWorkState::Accepted);
    assert_eq!(recovered.print_job_id.as_deref(), Some("job_report"));
    assert_eq!(
        recovered
            .print_job_observation
            .unwrap()
            .observation,
        observation
    );
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT state FROM commands WHERE command_id = $1")
            .bind(&command_id)
            .fetch_one(&pool)
            .await
            .unwrap(),
        "accepted"
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM managed_payloads")
            .fetch_one(&pool)
            .await
            .unwrap(),
        0
    );
    repository
        .record_publication("agt_example", "test", &state_publication(&accepted, &command_id), now)
        .await
        .unwrap();
    assert_eq!(
        controller
            .managed_work(&receipt.managed_work_id)
            .await
            .unwrap()
            .print_job_observation
            .unwrap()
            .observation,
        observation
    );
    let mut conflicting = observation.clone();
    conflicting.envelope_id = "ase_rewrite_version".into();
    conflicting.job.error_code = Some("different_error".into());
    assert!(
        repository
            .record_publication(
                "agt_example",
                "test",
                &state_publication(&conflicting, &command_id),
                now
            )
            .await
            .is_err()
    );
    conflicting.envelope_id = "ase_rewrite_terminal".into();
    conflicting.job.state = PrintJobState::Failed;
    conflicting.job.state_version = 4;
    conflicting.durable_state_sequence = 4;
    conflicting.envelope_sequence = 4;
    assert!(
        repository
            .record_publication(
                "agt_example",
                "test",
                &state_publication(&conflicting, &command_id),
                now
            )
            .await
            .is_err()
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM agent_state_observations")
            .fetch_one(&pool)
            .await
            .unwrap(),
        2
    );
}

impl ManagedPayloadKeyWrapper for TestKeyWrapper {
    fn wrap<'a>(
        &'a self,
        data_key: &'a [u8],
        _authenticated_data: &'a [u8],
    ) -> BoxFuture<'a, AppResult<TransitWrappedKey>> {
        Box::pin(async move {
            self.calls
                .fetch_add(1, Ordering::SeqCst);
            if self.unavailable.load(Ordering::SeqCst) {
                return Err(AppError::service_unavailable("Test key service is unavailable."));
            }
            *self.key.lock().unwrap() = data_key.to_vec();
            Ok(TransitWrappedKey { ciphertext: "vault:v1:test".into(), key_version: 1 })
        })
    }

    fn unwrap<'a>(
        &'a self,
        _wrapped_data_key: &'a str,
        _authenticated_data: &'a [u8],
    ) -> BoxFuture<'a, AppResult<zeroize::Zeroizing<Vec<u8>>>> {
        Box::pin(async move {
            if self.unavailable.load(Ordering::SeqCst) {
                return Err(AppError::service_unavailable("Test key service is unavailable."));
            }
            Ok(zeroize::Zeroizing::new(self.key.lock().unwrap().clone()))
        })
    }
}

async fn seed_database(pool: &PgPool) {
    sqlx::raw_sql(
        "INSERT INTO organizations (organization_id, name) VALUES ('org_example', 'Managed Work test');
         INSERT INTO sites (site_id, organization_id, name) VALUES ('site_example', 'org_example', 'Test site');
         INSERT INTO agents (agent_id, organization_id, site_id, key_id, jwk_thumbprint, public_jwk,
             namespace, protocol_version, controller_actions, enrolled_at, last_enrolled_at)
         VALUES ('agt_example', 'org_example', 'site_example', 'key_test', 'test',
             '{\"kty\":\"OKP\",\"crv\":\"Ed25519\",\"x\":\"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\"}',
             'inari/org_example/site_example/agt_example', '1.0', '[\"managed_work:dispatch\"]', now(), now());
         INSERT INTO devices (device_id, agent_id, site_id, kind, display_name, state, transport,
             hardware_fingerprint, capabilities, first_seen_at, last_seen_at)
         VALUES ('dev_printer', 'agt_example', 'site_example', 'printer', 'Test printer', 'online',
             'usb', 'printer_test', '[\"print\"]', now(), now());",
    )
    .execute(pool)
    .await
    .unwrap();
    let (_, public_key) = X25519HkdfSha256::derive_keypair(&[42; 32]);
    sqlx::query(
        "UPDATE agents SET dispatch_key = $1, protocol_version = $2 WHERE agent_id = 'agt_example'",
    )
    .bind(serde_json::json!({
        "key_id": "dispatch_test",
        "kem": "dhkem_x25519_hkdf_sha256",
        "public_key_base64url": URL_SAFE_NO_PAD.encode(public_key.to_bytes()),
    }))
    .bind(inari_gateway::protocol::ProtocolVersion::current().to_string())
    .execute(pool)
    .await
    .unwrap();
}

async fn seed_legacy_dispatch(pool: &PgPool, signer: &ManagedDispatchSigner) {
    let mut submission = submission();
    submission.work.print_intent_id = "pi_v1_legacy".parse().unwrap();
    submission.preflight_id = "mpf_legacy".parse().unwrap();
    let preflight = ManagedWorkPreflightRequest {
        contract_major: 1,
        scope: submission.work.scope.clone(),
        device_id: submission.work.device_id.clone(),
        operation: submission.work.document.operation(),
        binding: submission.work.origin.binding.clone(),
    };
    sqlx::query("INSERT INTO managed_work_preflights (preflight_id, request, dispatch_key_id, capability_digest,
        work_expires_at, idempotency_expires_at, submit_before, created_at)
        VALUES ('mpf_legacy', $1, 'dispatch_test', 'test', now() + interval '5 minutes',
        now() + interval '90 days', now() + interval '2 minutes', now())")
        .bind(serde_json::to_value(&preflight).unwrap()).execute(pool).await.unwrap();
    let key: serde_json::Value =
        sqlx::query_scalar("SELECT dispatch_key FROM agents WHERE agent_id = 'agt_example'")
            .fetch_one(pool)
            .await
            .unwrap();
    let recipient_key: DispatchEncryptionKey = serde_json::from_value(key).unwrap();
    let legacy_id = "mw_legacy".parse().unwrap();
    let now = Utc::now();
    let legacy_dispatch = serde_json::to_value(
        signer
            .command(
                inari_gateway::ManagedDispatchAllocation {
                    managed_work_id: &legacy_id,
                    sequence: 1,
                    command_id: "job_legacy",
                    message_id: "msg_legacy",
                    issued_at: now,
                    recipient_key: &recipient_key,
                    work_expires_at: now + TimeDelta::minutes(5),
                },
                "idempotency_legacy".into(),
                submission.clone(),
            )
            .unwrap(),
    )
    .unwrap();
    sqlx::query("INSERT INTO commands (command_id, agent_id, message_id, sequence, state, command,
        request_fingerprint, issued_at, updated_at) VALUES ('job_legacy', 'agt_example', 'msg_legacy', 1,
        'published', $1, $2, now(), now())")
        .bind(&legacy_dispatch).bind(vec![2u8; 32]).execute(pool).await.unwrap();
    sqlx::query("INSERT INTO managed_work (managed_work_id, preflight_id, organization_id, database_name,
        company_id, site_id, agent_id, device_id, print_intent_id, operation, media_type, state,
        binding_claim, payload_fingerprint, request_fingerprint, sealed_document, payload_bytes,
        message_key, expires_at, idempotency_expires_at, admitted_at, updated_at)
        VALUES ('mw_legacy', 'mpf_legacy', 'org_example', 'production', '7', 'site_example', 'agt_example',
        'dev_printer', 'pi_v1_legacy', 'report_pdf', 'application/pdf', 'dispatching', $1, $2, $3, $4, 8,
        'managed_work.dispatching', now() + interval '5 minutes', now() + interval '90 days', now(), now())")
        .bind(serde_json::to_value(submission.work.origin.binding).unwrap())
        .bind(vec![1u8; 32]).bind(vec![2u8; 32]).bind(&legacy_dispatch["payload"]["sealed_envelope"])
        .execute(pool).await.unwrap();
}

#[tokio::test]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn managed_payload_admission_replay_and_deletion() {
    let pool = PgPool::connect(&std::env::var("INARI_TEST_DATABASE_URL").unwrap())
        .await
        .unwrap();
    let database = DatabaseConnection::from(pool.clone());
    Migrator::up(&database, Some(5))
        .await
        .unwrap();
    seed_database(&pool).await;
    let repository = GatewayRepository::new(database.clone());
    let directory = tempfile::tempdir().unwrap();
    let signing_key_file = directory
        .path()
        .join("dispatch-key.der");
    std::fs::write(
        &signing_key_file,
        SigningKey::from_bytes(&[7; 32])
            .to_pkcs8_der()
            .unwrap()
            .as_bytes(),
    )
    .unwrap();
    let mut config = ManagedGatewayConfig { enabled: true, ..Default::default() };
    config.dispatch.enabled = true;
    config.dispatch.signing_key_id = Some("dispatch_test".into());
    config.dispatch.signing_key_file = Some(signing_key_file);
    let signer = ManagedDispatchSigner::load(&config.dispatch, "controller_test")
        .await
        .unwrap();
    seed_legacy_dispatch(&pool, &signer).await;
    let pending_upgrade = Migrator::up(&database, None)
        .await
        .unwrap_err();
    assert!(
        pending_upgrade
            .to_string()
            .contains("Managed Work is still pending")
    );
    let legacy_content_retained: bool = sqlx::query_scalar(
        "SELECT work.sealed_document IS NOT NULL AND delivery.command ->> 'type' = \
         'controller.command.dispatch_device_work' AND work.state = 'dispatching' \
         FROM managed_work AS work JOIN commands AS delivery ON delivery.command_id = 'job_legacy' \
         WHERE work.managed_work_id = 'mw_legacy'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(legacy_content_retained);
    sqlx::query("UPDATE managed_work SET expires_at = now() - interval '1 second' WHERE managed_work_id = 'mw_legacy'")
        .execute(&pool).await.unwrap();
    Migrator::up(&database, Some(2))
        .await
        .unwrap();
    sqlx::raw_sql(
        "UPDATE managed_work SET state = 'dispatching', expires_at = now() + interval '5 minutes',
             payload_deleted_at = NULL WHERE managed_work_id = 'mw_legacy';
         INSERT INTO managed_payloads (managed_work_id, ciphertext, nonce, wrapped_data_key,
             wrapping_key_version, authenticated_data_digest, payload_fingerprint, plaintext_bytes,
             expires_at, created_at)
         VALUES ('mw_legacy', decode('0102', 'hex'), decode(repeat('00', 12), 'hex'), 'vault:v1:legacy',
             1, decode(repeat('02', 32), 'hex'), decode(repeat('01', 32), 'hex'), 8,
             now() + interval '5 minutes', now());",
    )
    .execute(&pool)
    .await
    .unwrap();
    let fingerprint_upgrade = Migrator::up(&database, None)
        .await
        .unwrap_err();
    assert!(
        fingerprint_upgrade
            .to_string()
            .contains("Device Work fingerprint")
    );
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM managed_payloads")
            .fetch_one(&pool)
            .await
            .unwrap(),
        1
    );
    sqlx::query("UPDATE managed_work SET expires_at = now() - interval '1 second' WHERE managed_work_id = 'mw_legacy'")
        .execute(&pool).await.unwrap();
    Migrator::up(&database, None)
        .await
        .unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT COUNT(*) FROM managed_payloads")
            .fetch_one(&pool)
            .await
            .unwrap(),
        0
    );
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT encode(payload_fingerprint, 'hex') FROM managed_work WHERE managed_work_id = 'mw_legacy'")
            .fetch_one(&pool).await.unwrap(),
        "01".repeat(32)
    );
    assert_eq!(
        repository
            .managed_work(&"mw_legacy".parse().unwrap())
            .await
            .unwrap()
            .state,
        ManagedWorkState::RecoveryUncertain
    );
    let legacy_idempotency: String = sqlx::query_scalar(
        "SELECT idempotency_key FROM managed_work WHERE managed_work_id = 'mw_legacy'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(legacy_idempotency, "idempotency_legacy");
    let legacy_command: serde_json::Value =
        sqlx::query_scalar("SELECT command FROM commands WHERE command_id = 'job_legacy'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(legacy_command, serde_json::json!({"managed_work_id": "mw_legacy"}));

    let (zenoh, _) = ZenohSupervisor::new(ZenohConfig::default());
    let wrapper = Arc::new(TestKeyWrapper::default());
    let controller = ManagedGatewayController::new(
        config,
        OrganizationConfig { id: "org_example".parse().unwrap(), ..Default::default() },
        ZenohConfig::default(),
        zenoh,
        Some(repository.clone()),
        None,
        Some(Arc::new(ManagedWorkSecurity::new(
            signer,
            ManagedPayloadProtector::new(wrapper.clone()),
        ))),
    );
    let mut submission = submission();
    prepare_submission(&controller, &mut submission).await;
    let work_id = "mw_test".parse().unwrap();
    let receipt = controller
        .submit_managed_work(work_id, "idempotency_test".into(), submission.clone())
        .await
        .unwrap();
    assert_eq!(receipt.state, ManagedWorkState::Dispatching);
    assert_eq!(wrapper.calls.load(Ordering::SeqCst), 1);
    let payload_count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM managed_payloads")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(payload_count, 1);
    let stored_command: serde_json::Value = sqlx::query_scalar(
        "SELECT command FROM commands WHERE command ->> 'managed_work_id' = 'mw_test'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(stored_command, serde_json::json!({"managed_work_id": "mw_test"}));
    let (_, history) = controller
        .inner
        .store
        .command_history("agt_example", 2)
        .await
        .unwrap();
    let dispatch = controller
        .dispatch_message(&history[0])
        .await
        .unwrap();
    assert_eq!(
        controller
            .dispatch_message(&history[0])
            .await
            .unwrap(),
        dispatch
    );

    wrapper
        .unavailable
        .store(true, Ordering::SeqCst);
    let mut changed_work = submission.clone();
    changed_work.work.device_id = "dev_another".parse().unwrap();
    let conflict = controller
        .submit_managed_work("mw_retry".parse().unwrap(), "idempotency_test".into(), changed_work)
        .await;
    assert!(conflict.is_err(), "another Device cannot reuse the same document identity");
    assert_eq!(wrapper.calls.load(Ordering::SeqCst), 1);

    submission.preflight_id = "mpf_new".parse().unwrap();
    let replay = controller
        .submit_managed_work(
            "mw_retry".parse().unwrap(),
            "idempotency_test".into(),
            submission.clone(),
        )
        .await
        .unwrap();
    assert_eq!(replay.managed_work_id, receipt.managed_work_id);
    assert_eq!(wrapper.calls.load(Ordering::SeqCst), 1);

    let command_id: String = sqlx::query_scalar(
        "SELECT command_id FROM commands WHERE command ->> 'managed_work_id' = 'mw_test'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    let accepted: AgentPublication = serde_json::from_value(serde_json::json!({
        "type": "agent.command.accepted",
        "message_id": "accepted_test",
        "command_id": command_id,
        "accepted_at": Utc::now(),
        "job": {
            "managed_work_id": "mw_test", "print_intent_id": "pi_v1_test",
            "print_job_id": "pj_test", "device_id": "dev_printer", "state": "accepted",
            "state_version": 1, "replayed": false,
        },
        "detail": "accepted",
    }))
    .unwrap();
    let rejected: AgentPublication = serde_json::from_value(serde_json::json!({
        "type": "agent.command.rejected", "message_id": "unsigned_rejection", "command_id": command_id,
        "rejected_at": Utc::now(), "code": "invalid_work", "detail": "rejected",
    })).unwrap();
    let initial_command_state: String =
        sqlx::query_scalar("SELECT state FROM commands WHERE command_id = $1")
            .bind(&command_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    for publication in [&accepted, &rejected] {
        repository
            .record_publication("agt_example", "test", publication, Utc::now())
            .await
            .unwrap();
        let retained: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM managed_payloads WHERE managed_work_id = 'mw_test'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(retained, 1, "unsigned receipts cannot delete Managed Payloads");
        let work = controller
            .managed_work(&receipt.managed_work_id)
            .await
            .unwrap();
        assert_eq!(work.state, ManagedWorkState::Dispatching);
        assert!(work.print_job_id.is_none());
        assert_eq!(
            sqlx::query_scalar::<_, String>("SELECT state FROM commands WHERE command_id = $1")
                .bind(&command_id)
                .fetch_one(&pool)
                .await
                .unwrap(),
            initial_command_state
        );
    }

    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../inari-gateway/tests/fixtures/agent-state-observation.json"
    ))
    .unwrap();
    let key = &fixture["public_jwk"];
    sqlx::query("INSERT INTO agent_verification_keys (key_id, agent_id, purpose, jwk_thumbprint, public_jwk, registered_at) VALUES ($1, 'agt_example', 'agent_state', 'state-test', $2, now())")
        .bind(key["kid"].as_str().unwrap()).bind(key).execute(&pool).await.unwrap();
    let mut observation: AgentStateObservation =
        serde_json::from_value(fixture["claims"].clone()).unwrap();
    let work = controller
        .managed_work(&receipt.managed_work_id)
        .await
        .unwrap();
    let now = Utc::now();
    observation.agent_id = work.scope.agent_id.clone();
    observation.observed_at = now;
    observation.issued_at = now;
    observation.payload_fingerprint = format!("sha256:{}", sqlx::query_scalar::<_, String>("SELECT encode(payload_fingerprint, 'hex') FROM managed_work WHERE managed_work_id = 'mw_test'").fetch_one(&pool).await.unwrap());
    observation.job.managed_work_id = work.managed_work_id.clone();
    observation.job.print_intent_id = work.print_intent_id.clone();
    observation.job.print_job_id = "pj_test".into();
    observation.job.device_id = work.device_id.clone();
    observation.job.accepted_at = now;
    observation.job.expires_at = work.expires_at;
    observation.job.origin.organization_id = work.scope.organization_id.clone();
    observation.job.origin.site_id = work.scope.site_id.clone();
    observation.job.origin.database = work.scope.database.clone();
    observation.job.origin.company_id = work.scope.company_id.clone();
    observation.job.origin.report_route = submission.work.origin.route;
    observation.job.origin.source_model = "sale.order".into();
    observation.job.origin.record_ids = vec!["42".into()];
    observation.job.origin.report_binding_id = submission
        .work
        .origin
        .binding
        .report_binding_id
        .clone();
    observation.job.origin.report_action = submission
        .work
        .origin
        .binding
        .report_action_id
        .clone();
    repository
        .record_publication(
            "agt_example",
            "test",
            &state_publication(&observation, &command_id),
            now,
        )
        .await
        .unwrap();
    let remaining: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM managed_payloads")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(remaining, 0);
    let state = controller
        .managed_work(&receipt.managed_work_id)
        .await
        .unwrap();
    assert_eq!(state.state, ManagedWorkState::Accepted);
    assert_eq!(state.print_job_id.as_deref(), Some("pj_test"));
    let deleted: bool = sqlx::query_scalar(
        "SELECT payload_deleted_at IS NOT NULL FROM managed_work WHERE managed_work_id = 'mw_test'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert!(deleted);
    assert!(
        controller
            .dispatch_message(&history[0])
            .await
            .is_err()
    );
    repository
        .mark_command_published("agt_example", &command_id, Utc::now())
        .await
        .unwrap();
    let command_state: String =
        sqlx::query_scalar("SELECT state FROM commands WHERE command_id = $1")
            .bind(&command_id)
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(command_state, "accepted");
    let rejected: AgentPublication = serde_json::from_value(serde_json::json!({
        "type": "agent.command.rejected", "message_id": "conflicting_rejection", "command_id": command_id,
        "rejected_at": Utc::now(), "code": "invalid_work", "detail": "rejected",
    })).unwrap();
    repository
        .record_publication("agt_example", "test", &rejected, Utc::now())
        .await
        .unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, String>("SELECT state FROM commands WHERE command_id = $1")
            .bind(&command_id)
            .fetch_one(&pool)
            .await
            .unwrap(),
        "accepted"
    );
    assert_eq!(
        controller
            .managed_work(&receipt.managed_work_id)
            .await
            .unwrap()
            .state,
        ManagedWorkState::Accepted
    );

    let replay = controller
        .submit_managed_work(
            "mw_final".parse().unwrap(),
            "idempotency_test".into(),
            submission.clone(),
        )
        .await
        .unwrap();
    assert_eq!(replay.state, ManagedWorkState::Accepted);
    assert_eq!(wrapper.calls.load(Ordering::SeqCst), 1);

    wrapper
        .unavailable
        .store(false, Ordering::SeqCst);
    submission.work.print_intent_id = "pi_v1_expiry".parse().unwrap();
    prepare_submission(&controller, &mut submission).await;
    let expiring = controller
        .submit_managed_work("mw_expiry".parse().unwrap(), "idempotency_expiry".into(), submission)
        .await
        .unwrap();
    sqlx::query("UPDATE managed_work SET expires_at = now() - interval '1 second' WHERE managed_work_id = 'mw_expiry'").execute(&pool).await.unwrap();
    assert_eq!(
        repository
            .expire_managed_payloads(Utc::now())
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        repository
            .expire_managed_payloads(Utc::now())
            .await
            .unwrap(),
        0
    );
    assert_eq!(
        controller
            .managed_work(&expiring.managed_work_id)
            .await
            .unwrap()
            .state,
        ManagedWorkState::RecoveryUncertain
    );
    let remaining: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM managed_payloads")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(remaining, 0);
}
