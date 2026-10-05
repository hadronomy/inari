use std::time::Duration;

use chrono::{DateTime, SubsecRound, Utc};
use inari_gateway::audit::{AuditAction, AuditOutcome, AuditResource};
use inari_gateway::protocol::{
    AgentPublication, DeviceClass, DeviceKind, DeviceState, GatewaySnapshot,
};
use inari_gateway::{GatewayError, GatewayRepository, InvitationAttemptLimit};
use inari_migration::{Migrator, MigratorTrait};
use serde_json::{Value, json};
use sqlx::PgPool;

use super::enrollment_tests::{
    create_scope, enrollment, jwk, live_invitation, operator, snapshot, test_database,
};

const LIMIT: InvitationAttemptLimit =
    InvitationAttemptLimit { window: Duration::from_secs(60), max_failures: 3 };

fn item(device_id: &str, identity_digest: &str) -> Value {
    json!({
        "device_id": device_id, "identity_digest": identity_digest,
        "kind": "printer", "device_class": "physical", "display_name": "POS-80",
        "system_name": "POS-80", "driver_key": "windows.spooler",
        "connection_state": "online", "transport": "spooler",
        "capabilities": ["raw", "text"], "metadata": {},
    })
}

fn inventory(generated_at: DateTime<Utc>, items: Vec<Value>) -> GatewaySnapshot {
    let mut snapshot = snapshot();
    snapshot.generated_at = generated_at;
    snapshot.runtime.inventory = serde_json::from_value(json!({"devices": items})).unwrap();
    snapshot
}

fn publication(message_id: &str, snapshot: GatewaySnapshot) -> AgentPublication {
    AgentPublication::StatusSnapshot { message_id: message_id.into(), snapshot: Box::new(snapshot) }
}

async fn publication_count(pool: &PgPool) -> i64 {
    sqlx::query_scalar("SELECT count(*) FROM publications")
        .fetch_one(pool)
        .await
        .unwrap()
}

#[tokio::test]
#[ignore = "requires a fresh INARI_TEST_DATABASE_URL database"]
async fn device_inventory_is_scoped_ordered_and_atomic() {
    let (pool, database) = test_database().await;
    let before_projection = u32::try_from(Migrator::migrations().len() - 1).unwrap();
    Migrator::up(&database, Some(before_projection))
        .await
        .unwrap();
    create_scope(&pool).await;
    sqlx::raw_sql(
        "INSERT INTO agents (agent_id, organization_id, site_id, key_id, jwk_thumbprint,
             public_jwk, namespace, protocol_version, controller_actions, enrolled_at, last_enrolled_at)
         VALUES ('agt_legacy', 'org_keys', 'site_keys', 'legacy_key', 'legacy', '{}',
             'legacy_namespace', '1.0', '[]', now(), now());
         INSERT INTO devices (device_id, agent_id, site_id, kind, display_name, state, transport,
             hardware_fingerprint, capabilities, first_seen_at, last_seen_at)
         VALUES ('dev_legacy', 'agt_legacy', 'site_keys', 'printer', 'Legacy queue', 'online',
             'spooler', 'unverified fingerprint', '[]', now(), now());
         INSERT INTO managed_work_preflights (preflight_id, request, dispatch_key_id,
             capability_digest, work_expires_at, idempotency_expires_at, submit_before, created_at)
         VALUES ('preflight_legacy', '{}', 'legacy_key', 'legacy', now(), now(), now(), now());
         INSERT INTO managed_work (managed_work_id, preflight_id, organization_id,
             database_name, company_id, site_id, agent_id, device_id, print_intent_id,
             operation, media_type, state, binding_claim, payload_fingerprint,
             request_fingerprint, payload_bytes, message_key, expires_at,
             idempotency_expires_at, admitted_at, updated_at, idempotency_key)
         VALUES ('work_legacy', 'preflight_legacy', 'org_keys', 'odoo', '1', 'site_keys',
             'agt_legacy', 'dev_legacy', 'intent_legacy', 'report_pdf', 'application/pdf',
             'expired', '{}', decode(repeat('aa', 32), 'hex'), decode(repeat('bb', 32), 'hex'),
             1, 'message_legacy', now(), now(), now(), now(), 'idempotency_legacy');"
    ).execute(&pool).await.unwrap();
    Migrator::up(&database, None)
        .await
        .unwrap();
    let projected: i64 = sqlx::query_scalar("SELECT count(*) FROM devices")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(projected, 0);
    let historical_device: String = sqlx::query_scalar(
        "SELECT device_id FROM managed_work WHERE managed_work_id = 'work_legacy'",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(historical_device, "dev_legacy");

    let repository = GatewayRepository::new(database);
    let first_time = Utc::now().trunc_subsecs(6);
    let identity = "a".repeat(64);
    let original = inventory(first_time, vec![item("dev_queue", &identity)]);
    let first_agent = enrollment("agt_inventory", jwk(20, false), jwk(21, true));
    let first_code = live_invitation(&repository).await;
    repository
        .enroll_agent(&first_code, first_agent.clone(), &original, LIMIT, || Ok(()))
        .await
        .unwrap();
    let agent_id = first_agent.agent_id.clone();
    let devices = repository
        .devices(&agent_id)
        .await
        .unwrap();
    assert_eq!(devices.len(), 1);
    assert_eq!(devices[0].display_name, "POS-80");
    assert_eq!(devices[0].device_class, DeviceClass::Physical);
    assert!(devices[0].capabilities.is_empty());
    let stored_digest: String = sqlx::query_scalar(
        "SELECT identity_digest FROM devices WHERE agent_id = $1 AND device_id = 'dev_queue'",
    )
    .bind(agent_id.as_str())
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(stored_digest, identity);

    let newer_time = first_time + chrono::Duration::seconds(10);
    let mut renamed = item("dev_queue", &identity);
    renamed["display_name"] = json!("Front counter");
    let newer = publication("inventory_new", inventory(newer_time, vec![renamed]));
    repository
        .record_publication(agent_id.as_str(), "status", &newer, Utc::now())
        .await
        .unwrap();
    let first_seen: DateTime<Utc> = sqlx::query_scalar(
        "SELECT first_seen_at FROM devices WHERE agent_id = $1 AND device_id = 'dev_queue'",
    )
    .bind(agent_id.as_str())
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(first_seen, first_time);
    let current = repository
        .devices(&agent_id)
        .await
        .unwrap();
    assert_eq!(current[0].display_name, "Front counter");
    assert_eq!(current[0].last_seen_at, newer_time);

    let old = publication("inventory_old", original.clone());
    repository
        .record_publication(agent_id.as_str(), "status", &old, Utc::now())
        .await
        .unwrap();
    repository
        .record_publication(agent_id.as_str(), "status", &newer, Utc::now())
        .await
        .unwrap();
    repository
        .record_publication(agent_id.as_str(), "status", &old, Utc::now())
        .await
        .unwrap();
    assert_eq!(
        repository
            .devices(&agent_id)
            .await
            .unwrap()[0]
            .display_name,
        "Front counter"
    );
    let latest: Value =
        sqlx::query_scalar("SELECT latest_snapshot FROM invitations WHERE invitation_id = $1")
            .bind(first_code.id().as_str())
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(latest["runtime"]["inventory"]["devices"][0]["display_name"], "Front counter");
    assert_eq!(
        repository
            .latest_status(agent_id.as_str())
            .await
            .unwrap()
            .unwrap()
            .message_id,
        "inventory_new"
    );

    let second_agent = enrollment("agt_inventory_two", jwk(22, false), jwk(23, true));
    let second_code = live_invitation(&repository).await;
    repository
        .enroll_agent(&second_code, second_agent.clone(), &original, LIMIT, || Ok(()))
        .await
        .unwrap();
    assert_eq!(
        repository
            .devices(&second_agent.agent_id)
            .await
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        repository
            .devices(&agent_id)
            .await
            .unwrap()[0]
            .display_name,
        "Front counter"
    );
    let sites = repository
        .sites(&first_agent.organization_id)
        .await
        .unwrap();
    assert_eq!(
        sites
            .iter()
            .find(|site| site.site_id.as_str() == "site_keys")
            .unwrap()
            .device_count,
        2
    );
    let target = repository
        .managed_work_target("org_keys", "site_keys", second_agent.agent_id.as_str(), "dev_queue")
        .await
        .unwrap();
    assert_eq!(target.agent_id, second_agent.agent_id);

    for (index, invalid) in [
        vec![item("dev_queue", &"b".repeat(64))],
        vec![item("dev_other_identity", &identity)],
        vec![item("dev_new", &"c".repeat(64)), item("dev_queue", &"b".repeat(64))],
        vec![item("dev_queue", &identity), item("dev_queue", &identity)],
        vec![{
            let mut value = item("dev_queue", &identity);
            value["transport"] = json!("unknown");
            value
        }],
        vec![{
            let mut value = item("dev_queue", &identity);
            value["capabilities"] = json!(["certified"]);
            value
        }],
        vec![{
            let mut value = item("dev_queue", &identity);
            value["identity_digest"] = json!("bad");
            value
        }],
        vec![{
            let mut value = item("dev_queue", &identity);
            value
                .as_object_mut()
                .unwrap()
                .remove("transport");
            value
        }],
    ]
    .into_iter()
    .enumerate()
    {
        let before = publication_count(&pool).await;
        let audit_before = repository
            .audit_events(&first_agent.organization_id, None, 100)
            .await
            .unwrap()
            .len();
        let invalid = publication(
            &format!("invalid_{index}"),
            inventory(newer_time + chrono::Duration::seconds(1), invalid),
        );
        let rejected = repository
            .record_publication(agent_id.as_str(), "status", &invalid, Utc::now())
            .await;
        assert!(matches!(rejected, Err(GatewayError::InvalidInput(_))));
        let audit = repository
            .audit_events(&first_agent.organization_id, None, 100)
            .await
            .unwrap();
        assert_eq!(audit.len(), audit_before + 1);
        assert_eq!(audit[0].action, AuditAction::AgentInventoryRejected);
        assert_eq!(audit[0].outcome, AuditOutcome::Denied);
        assert_eq!(audit[0].resource, AuditResource::Agent { agent_id: agent_id.clone() });
        assert!(audit[0].request_id.is_none());
        assert_eq!(publication_count(&pool).await, before);
        assert_eq!(
            repository
                .devices(&agent_id)
                .await
                .unwrap()[0]
                .display_name,
            "Front counter"
        );
        assert_eq!(
            repository
                .devices(&agent_id)
                .await
                .unwrap()
                .len(),
            1
        );
    }

    let withdrawn = publication(
        "inventory_withdrawn",
        inventory(newer_time + chrono::Duration::seconds(2), vec![]),
    );
    repository
        .record_publication(agent_id.as_str(), "status", &withdrawn, Utc::now())
        .await
        .unwrap();
    assert!(
        repository
            .devices(&agent_id)
            .await
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        repository
            .devices(&second_agent.agent_id)
            .await
            .unwrap()
            .len(),
        1
    );

    let next_code = inari_gateway::onboarding::InvitationCode::generate().unwrap();
    repository
        .create_invitation(
            &next_code,
            &first_agent.organization_id,
            &"site_other".parse().unwrap(),
            None,
            Utc::now()..Utc::now() + chrono::Duration::hours(1),
            &operator(),
        )
        .await
        .unwrap();
    let mut rotated = enrollment("agt_inventory", jwk(24, false), jwk(25, true));
    rotated.site_id = "site_other".parse().unwrap();
    rotated.namespace = "inari/org_keys/site_other/agt_inventory".into();
    let new_scope_snapshot =
        inventory(newer_time + chrono::Duration::seconds(3), vec![item("dev_queue", &identity)]);
    repository
        .enroll_agent(&next_code, rotated, &new_scope_snapshot, LIMIT, || Ok(()))
        .await
        .unwrap();
    assert_eq!(
        repository
            .devices(&agent_id)
            .await
            .unwrap()[0]
            .site_id
            .as_str(),
        "site_other"
    );
    assert!(
        repository
            .latest_status(agent_id.as_str())
            .await
            .unwrap()
            .is_none()
    );
    repository
        .record_publication(agent_id.as_str(), "status", &newer, Utc::now())
        .await
        .unwrap();
    assert_eq!(
        repository
            .devices(&agent_id)
            .await
            .unwrap()[0]
            .site_id
            .as_str(),
        "site_other"
    );

    // Publication must not hold an Agent lock while it waits for the invitation.
    let mut boundary = pool.begin().await.unwrap();
    sqlx::query("SELECT invitation_id FROM invitations WHERE invitation_id = $1 FOR UPDATE")
        .bind(next_code.id().as_str())
        .fetch_one(&mut *boundary)
        .await
        .unwrap();
    let late = publication(
        "inventory_late",
        inventory(newer_time + chrono::Duration::seconds(4), vec![item("dev_queue", &identity)]),
    );
    let publishing_repository = repository.clone();
    let publishing_agent = agent_id.clone();
    let publishing = tokio::spawn(async move {
        publishing_repository
            .record_publication(publishing_agent.as_str(), "status", &late, Utc::now())
            .await
    });
    let mut waiting = false;
    for _ in 0..100 {
        let locks: i64 = sqlx::query_scalar("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'")
            .fetch_one(&pool).await.unwrap();
        if locks > 0 {
            waiting = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert!(waiting);
    tokio::time::timeout(
        Duration::from_secs(2),
        sqlx::query("SELECT agent_id FROM agents WHERE agent_id = $1 FOR UPDATE")
            .bind(agent_id.as_str())
            .fetch_one(&mut *boundary),
    )
    .await
    .unwrap()
    .unwrap();
    boundary.commit().await.unwrap();
    tokio::time::timeout(Duration::from_secs(5), publishing)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    repository
        .retire_agent_credentials(&first_agent.organization_id, &agent_id, &operator())
        .await
        .unwrap();
    let after_retirement = publication(
        "inventory_retired",
        inventory(newer_time + chrono::Duration::seconds(5), vec![item("dev_queue", &identity)]),
    );
    repository
        .record_publication(agent_id.as_str(), "status", &after_retirement, Utc::now())
        .await
        .unwrap();
    assert!(
        repository
            .devices(&agent_id)
            .await
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        repository
            .devices(&second_agent.agent_id)
            .await
            .unwrap()
            .len(),
        1
    );

    let mut virtual_printer = item("dev_queue", &identity);
    virtual_printer["device_class"] = json!("virtual");
    virtual_printer["connection_state"] = json!("offline");
    let mut display = item("dev_display", &"d".repeat(64));
    display["kind"] = json!("display");
    display["capabilities"] = json!([]);
    repository
        .record_publication(
            second_agent.agent_id.as_str(),
            "status",
            &publication(
                "inventory_classes",
                inventory(
                    newer_time + chrono::Duration::seconds(6),
                    vec![virtual_printer, display],
                ),
            ),
            Utc::now(),
        )
        .await
        .unwrap();
    let classes = repository
        .devices(&second_agent.agent_id)
        .await
        .unwrap();
    assert_eq!(classes.len(), 2);
    let virtual_printer = classes
        .iter()
        .find(|device| device.device_id.as_str() == "dev_queue")
        .unwrap();
    assert_eq!(virtual_printer.device_class, DeviceClass::Virtual);
    assert_eq!(virtual_printer.state, DeviceState::Offline);
    assert!(virtual_printer.capabilities.is_empty());
    let display = classes
        .iter()
        .find(|device| device.device_id.as_str() == "dev_display")
        .unwrap();
    assert_eq!(display.kind, DeviceKind::Display);
    assert_eq!(display.device_class, DeviceClass::Physical);
}
