use chrono::{Duration, Utc};
use ed25519_dalek::SigningKey;
use serde_json::json;

use crate::{AgentAdmission, Policy, PolicyStore, RouterConfig, RouterError, SignedPolicy};

pub(crate) const AGENT_A: &str = "agt_aaaaaaaaaaaaaaaaaaaaaaaa";
pub(crate) const AGENT_B: &str = "agt_bbbbbbbbbbbbbbbbbbbbbbbb";

pub(crate) fn policy(generation: u64) -> Policy {
    let now = Utc::now();
    Policy {
        version: 1,
        fleet_id: "fleet-test".into(),
        generation,
        issued_at: now,
        not_before: now,
        expires_at: now + Duration::minutes(3),
        namespace_prefix: "iot/v1/agents".into(),
        trusted_peer_common_names: vec!["controller-test".into(), "router-test".into()],
        agents: [AGENT_A, AGENT_B]
            .map(|name| AgentAdmission {
                common_name: name.into(),
                namespace: format!("iot/v1/agents/{name}"),
            })
            .to_vec(),
    }
}

pub(crate) fn router() -> RouterConfig {
    RouterConfig {
        id: "1234567890abcdef1234567890abcdef".into(),
        listen_endpoint: "tls/[::]:7447".into(),
        probe_endpoint: "tls/localhost:7447".into(),
        peer_endpoints: vec![],
        root_ca_file: "/test/root.pem".into(),
        certificate_file: "/test/router.pem".into(),
        private_key_file: "/test/router.key".into(),
        probe_certificate_file: "/test/probe.pem".into(),
        probe_private_key_file: "/test/probe.key".into(),
    }
}

#[test]
fn signature_binds_every_policy_fact_and_fleet() {
    let key = SigningKey::from_bytes(&[11; 32]);
    let signed = SignedPolicy::sign(policy(1), &key).unwrap();
    let verified = signed
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap();
    assert!(
        verified
            .require_current(Utc::now())
            .is_ok()
    );
    assert!(
        signed
            .verify(&key.verifying_key(), "another-fleet")
            .is_err()
    );
    assert!(
        signed
            .verify(&SigningKey::from_bytes(&[12; 32]).verifying_key(), "fleet-test")
            .is_err()
    );
    let mut changed = signed.clone();
    changed.policy.generation = 2;
    assert!(
        changed
            .verify(&key.verifying_key(), "fleet-test")
            .is_err()
    );
    changed = signed.clone();
    changed.policy.agents.pop();
    assert!(
        changed
            .verify(&key.verifying_key(), "fleet-test")
            .is_err()
    );
    changed = signed.clone();
    changed
        .signature
        .replace_range(..2, "00");
    assert!(
        changed
            .verify(&key.verifying_key(), "fleet-test")
            .is_err()
    );
}

#[test]
fn policy_rejects_ambiguous_or_excessive_authority() {
    let key = SigningKey::from_bytes(&[11; 32]);
    let base = policy(1);
    let mut invalid = Vec::new();
    let mut value = base.clone();
    value.namespace_prefix = "iot/**".into();
    invalid.push(value);
    let mut value = base.clone();
    value.namespace_prefix = "iot//agents".into();
    invalid.push(value);
    let mut value = base.clone();
    value.agents[0].namespace = value.agents[1].namespace.clone();
    invalid.push(value);
    let mut value = base.clone();
    value.agents[0].common_name = "agt_*".into();
    invalid.push(value);
    let mut value = base.clone();
    value
        .agents
        .push(value.agents[0].clone());
    invalid.push(value);
    let mut value = base.clone();
    value
        .trusted_peer_common_names
        .push(AGENT_A.into());
    invalid.push(value);
    let mut value = base.clone();
    value
        .trusted_peer_common_names
        .push("*".into());
    invalid.push(value);
    let mut value = base.clone();
    value
        .trusted_peer_common_names
        .push("controller-test".into());
    invalid.push(value);
    let mut value = base.clone();
    value.trusted_peer_common_names.clear();
    invalid.push(value);
    let mut value = base.clone();
    value.generation = 0;
    invalid.push(value);
    let mut value = base.clone();
    value.generation = 9_007_199_254_740_992;
    invalid.push(value);
    let mut value = base.clone();
    value.version = 2;
    invalid.push(value);
    let mut value = base.clone();
    value.expires_at = value.issued_at + Duration::minutes(6);
    invalid.push(value);
    let mut value = base.clone();
    value.not_before = value.expires_at;
    invalid.push(value);
    for value in invalid {
        assert!(SignedPolicy::sign(value, &key).is_err());
    }
}

#[test]
fn time_boundaries_fail_closed_without_losing_generation() {
    let key = SigningKey::from_bytes(&[11; 32]);
    let signed = SignedPolicy::sign(policy(1), &key).unwrap();
    let verified = signed
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap();
    assert!(
        verified
            .require_current(verified.policy().not_before - Duration::nanoseconds(1))
            .is_err()
    );
    assert!(
        verified
            .require_current(verified.policy().not_before)
            .is_ok()
    );
    assert!(
        verified
            .require_current(verified.policy().expires_at)
            .is_err()
    );
    assert_eq!(verified.policy().generation, 1);
}

#[test]
fn generation_retries_require_the_same_digest() {
    let key = SigningKey::from_bytes(&[11; 32]);
    let base = policy(2);
    let current = SignedPolicy::sign(base.clone(), &key)
        .unwrap()
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap();
    assert!(!current.advances(&current).unwrap());
    let mut value = base.clone();
    value.agents.pop();
    let conflicting = SignedPolicy::sign(value, &key)
        .unwrap()
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap();
    assert!(matches!(conflicting.advances(&current), Err(RouterError::GenerationConflict)));
    let mut value = base.clone();
    value.generation = 1;
    let stale = SignedPolicy::sign(value, &key)
        .unwrap()
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap();
    assert!(stale.advances(&current).is_err());
    let mut value = base;
    value.generation = 3;
    let next = SignedPolicy::sign(value, &key)
        .unwrap()
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap();
    assert!(next.advances(&current).unwrap());
}

#[test]
fn storage_preserves_high_water_mark_and_excludes_another_owner() {
    let directory = tempfile::tempdir().unwrap();
    let key = SigningKey::from_bytes(&[11; 32]);
    let mut value = policy(9);
    value.issued_at -= Duration::hours(1);
    value.not_before -= Duration::hours(1);
    value.expires_at -= Duration::hours(1);
    let verified = SignedPolicy::sign(value, &key)
        .unwrap()
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap();
    let store = PolicyStore::open(directory.path()).unwrap();
    assert!(PolicyStore::open(directory.path()).is_err());
    assert!(
        store
            .load(&key.verifying_key(), "fleet-test")
            .unwrap()
            .is_none()
    );
    store.persist(&verified).unwrap();
    drop(store);
    let reopened = PolicyStore::open(directory.path()).unwrap();
    let recovered = reopened
        .load(&key.verifying_key(), "fleet-test")
        .unwrap()
        .unwrap();
    assert_eq!(recovered.digest(), verified.digest());
    assert_eq!(recovered.policy().generation, 9);
    assert!(
        recovered
            .require_current(Utc::now())
            .is_err()
    );
    assert!(
        reopened
            .load(&key.verifying_key(), "wrong-fleet")
            .is_err()
    );
    std::fs::write(directory.path().join("policy.json"), b"invalid").unwrap();
    assert!(
        reopened
            .load(&key.verifying_key(), "fleet-test")
            .is_err()
    );
}

#[test]
fn generated_acl_has_exact_tls_subjects_and_disjoint_agent_rules() {
    let key = SigningKey::from_bytes(&[11; 32]);
    let verified = SignedPolicy::sign(policy(1), &key)
        .unwrap()
        .verify(&key.verifying_key(), "fleet-test")
        .unwrap();
    let config = router().render(&verified).unwrap();
    let acl = &config["access_control"];
    assert_eq!(acl["default_permission"], "deny");
    for subject in acl["subjects"].as_array().unwrap() {
        assert!(
            !subject["cert_common_names"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert_eq!(subject["link_protocols"], json!(["tls"]));
    }
    for agent in [AGENT_A, AGENT_B] {
        let other = if agent == AGENT_A { AGENT_B } else { AGENT_A };
        let rules = acl["rules"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|rule| {
                rule["id"]
                    .as_str()
                    .unwrap()
                    .starts_with(agent)
            });
        assert_eq!(rules.clone().count(), 6);
        for rule in rules {
            for expression in rule["key_exprs"].as_array().unwrap() {
                let expression = expression.as_str().unwrap();
                assert!(expression.starts_with(&format!("iot/v1/agents/{agent}/")));
                assert!(!expression.contains(other));
            }
        }
    }
}

#[test]
fn unknown_fields_and_duplicate_struct_fields_are_rejected() {
    let key = SigningKey::from_bytes(&[11; 32]);
    let signed = SignedPolicy::sign(policy(1), &key).unwrap();
    let mut value = serde_json::to_value(signed).unwrap();
    value["policy"]["unsigned_authority"] = json!(true);
    assert!(serde_json::from_value::<SignedPolicy>(value).is_err());
    let duplicate = r#"{"version":1,"version":1}"#;
    assert!(serde_json::from_str::<Policy>(duplicate).is_err());
}

#[tokio::test]
async fn failed_activation_keeps_the_new_generation_and_blocks_rollback() {
    let directory = tempfile::tempdir().unwrap();
    let key = SigningKey::from_bytes(&[11; 32]);
    let (handle, supervisor) = crate::supervisor::RouterSupervisor::new(
        PolicyStore::open(directory.path()).unwrap(),
        key.verifying_key(),
        "fleet-test".into(),
        directory.path().join("absent-zenohd"),
        router(),
    )
    .unwrap();
    let (shutdown, receiver) = tokio::sync::watch::channel(false);
    let task = tokio::spawn(supervisor.run(receiver));
    let first = SignedPolicy::sign(policy(2), &key).unwrap();
    assert!(
        handle
            .apply(first.clone())
            .await
            .is_err()
    );
    assert!(!handle.status().ready);
    assert_eq!(handle.status().generation, Some(2));
    assert!(matches!(
        handle
            .apply(SignedPolicy::sign(policy(1), &key).unwrap())
            .await,
        Err(RouterError::GenerationConflict)
    ));
    shutdown.send(true).unwrap();
    task.await.unwrap().unwrap();
    let stored = PolicyStore::open(directory.path())
        .unwrap()
        .load(&key.verifying_key(), "fleet-test")
        .unwrap()
        .unwrap();
    assert_eq!(
        stored.digest(),
        first
            .verify(&key.verifying_key(), "fleet-test")
            .unwrap()
            .digest()
    );
}

#[test]
fn startup_rejects_weak_signing_keys_and_invalid_configuration() {
    let directory = tempfile::tempdir().unwrap();
    let mut identity_point = [0; 32];
    identity_point[0] = 1;
    let weak = ed25519_dalek::VerifyingKey::from_bytes(&identity_point).unwrap();
    assert!(matches!(
        crate::supervisor::RouterSupervisor::new(
            PolicyStore::open(directory.path()).unwrap(),
            weak,
            "fleet-test".into(),
            "zenohd".into(),
            router(),
        ),
        Err(RouterError::InvalidSignature)
    ));
    let mut invalid = router();
    invalid.id = "00000000000000000000000000000000".into();
    assert!(invalid.validate().is_err());
    invalid = router();
    invalid.probe_endpoint = "tls/localhost:7447#verify_name_on_connect=false".into();
    assert!(invalid.validate().is_err());
    invalid = router();
    invalid
        .peer_endpoints
        .push("tcp/localhost:7447".into());
    assert!(invalid.validate().is_err());
}

#[tokio::test]
async fn an_existing_shutdown_signal_prevents_startup() {
    let directory = tempfile::tempdir().unwrap();
    let (handle, supervisor) = crate::supervisor::RouterSupervisor::new(
        PolicyStore::open(directory.path()).unwrap(),
        SigningKey::from_bytes(&[11; 32]).verifying_key(),
        "fleet-test".into(),
        "absent-zenohd".into(),
        router(),
    )
    .unwrap();
    let (_shutdown, receiver) = tokio::sync::watch::channel(true);
    supervisor.run(receiver).await.unwrap();
    assert!(!handle.status().ready);
    assert!(matches!(
        handle
            .apply(SignedPolicy::sign(policy(1), &SigningKey::from_bytes(&[11; 32])).unwrap())
            .await,
        Err(RouterError::Unavailable)
    ));
}
