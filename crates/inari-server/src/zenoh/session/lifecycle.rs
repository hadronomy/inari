use std::str::FromStr;

use bytes::Bytes;
use serde::Serialize;
use serde_json::{Value, json};
use zenoh::bytes::{Encoding, ZBytes};
use zenoh::config::EndPoint;
use zenoh::{Config, Session, open};

use super::super::KeyExpression;
use crate::config::ZenohAclPermission;
use crate::config::ZenohConfig;
use crate::error::{AppError, AppResult};

pub(crate) async fn open_session(config: &ZenohConfig) -> AppResult<Session> {
    let mut zenoh_config = Config::default();
    configure_zenoh(&mut zenoh_config, config)?;

    open(zenoh_config)
        .await
        .map_err(|source| {
            AppError::service_unavailable(format!("Failed to open the Zenoh session: {source}"))
        })
}

pub(crate) async fn close_session(session: Session) {
    if let Err(source) = session.close().await {
        tracing::warn!(error = %source, "failed to close Zenoh session cleanly");
    }
}

pub(crate) async fn publish(
    session: &Session,
    key: &KeyExpression,
    payload: Bytes,
    encoding: Encoding,
    attachment: Option<Bytes>,
) -> AppResult<()> {
    let payload = ZBytes::from(payload);
    session
        .put(key, payload)
        .encoding(encoding)
        .attachment(attachment.map(ZBytes::from))
        .await
        .map_err(|_| AppError::service_unavailable("Zenoh publish failed."))?;

    Ok(())
}

pub(crate) async fn delete(session: &Session, key: &KeyExpression) -> AppResult<()> {
    session
        .delete(key)
        .await
        .map_err(|_| AppError::service_unavailable("Zenoh delete failed."))?;

    Ok(())
}

fn configure_zenoh(config: &mut Config, settings: &ZenohConfig) -> AppResult<()> {
    insert_json5_serialized(config, "mode", settings.mode)?;
    insert_json5_serialized(config, "adminspace/enabled", settings.admin_space.enabled)?;
    insert_json5_serialized(config, "adminspace/permissions/read", settings.admin_space.read)?;
    insert_json5_serialized(config, "adminspace/permissions/write", settings.admin_space.write)?;

    if !settings.connect_endpoints.is_empty() {
        apply_endpoints(config, "connect/endpoints", "connect", &settings.connect_endpoints)?;
    }

    if !settings.listen_endpoints.is_empty() {
        apply_endpoints(config, "listen/endpoints", "listen", &settings.listen_endpoints)?;
    }

    apply_tls(config, settings)?;
    apply_access_control(config, settings)?;

    Ok(())
}

fn apply_tls(config: &mut Config, settings: &ZenohConfig) -> AppResult<()> {
    if let Some(path) = &settings.tls.root_ca_certificate {
        insert_json5_serialized(
            config,
            "transport/link/tls/root_ca_certificate",
            path.display().to_string(),
        )?;
    }
    if let Some(path) = &settings.tls.listen_private_key {
        insert_json5_serialized(
            config,
            "transport/link/tls/listen_private_key",
            path.display().to_string(),
        )?;
    }
    if let Some(path) = &settings.tls.listen_certificate {
        insert_json5_serialized(
            config,
            "transport/link/tls/listen_certificate",
            path.display().to_string(),
        )?;
    }
    if let Some(path) = &settings.tls.connect_private_key {
        insert_json5_serialized(
            config,
            "transport/link/tls/connect_private_key",
            path.display().to_string(),
        )?;
    }
    if let Some(path) = &settings.tls.connect_certificate {
        insert_json5_serialized(
            config,
            "transport/link/tls/connect_certificate",
            path.display().to_string(),
        )?;
    }
    if settings.tls.enable_mtls {
        insert_json5_serialized(config, "transport/link/tls/enable_mtls", true)?;
    }
    if settings.tls.close_link_on_expiration {
        insert_json5_serialized(config, "transport/link/tls/close_link_on_expiration", true)?;
    }
    Ok(())
}

fn apply_access_control(config: &mut Config, settings: &ZenohConfig) -> AppResult<()> {
    if !settings.access_control.enabled {
        return Ok(());
    }
    let default_permission = match settings
        .access_control
        .default_permission
    {
        ZenohAclPermission::Allow => "allow",
        ZenohAclPermission::Deny => "deny",
    };
    let mut access_control = json!({
        "enabled": true,
        "default_permission": default_permission,
        "rules": [],
        "subjects": [],
        "policies": [],
    });
    if let Some(namespace_prefix) = &settings
        .access_control
        .managed_gateway_namespace_prefix
    {
        let namespace_prefix = namespace_prefix.trim_end_matches('/');
        let subject = if settings
            .access_control
            .managed_gateway_cert_common_names
            .is_empty()
        {
            json!({"id": "managed-agents"})
        } else {
            json!({
                "id": "managed-agents",
                "cert_common_names": settings.access_control.managed_gateway_cert_common_names.clone(),
            })
        };
        access_control["rules"] = json!([
            {
                "id": "managed-agent-publications",
                "permission": "allow",
                "flows": ["ingress"],
                "messages": ["put"],
                "key_exprs": [
                    format!("{namespace_prefix}/*/presence/agent"),
                    format!("{namespace_prefix}/*/status/latest"),
                    format!("{namespace_prefix}/*/results/*"),
                    format!("{namespace_prefix}/*/events/*"),
                    format!("{namespace_prefix}/*/errors/*"),
                ],
            },
            {
                "id": "managed-agent-presence",
                "permission": "allow",
                "flows": ["ingress"],
                "messages": ["liveliness_token"],
                "key_exprs": [format!("{namespace_prefix}/*/presence/agent")],
            },
            {
                "id": "managed-controller-publication-subscription",
                "permission": "allow",
                "flows": ["egress"],
                "messages": ["declare_subscriber"],
                "key_exprs": [format!("{namespace_prefix}/*/**")],
            },
            {
                "id": "managed-agent-state-commit",
                "permission": "allow",
                "flows": ["ingress"],
                "messages": ["query"],
                "key_exprs": [format!("{namespace_prefix}/*/state/commit")],
            },
            {
                "id": "managed-agent-state-receipt",
                "permission": "allow",
                "flows": ["egress"],
                "messages": ["reply", "declare_queryable"],
                "key_exprs": [format!("{namespace_prefix}/*/state/commit")],
            },
            {
                "id": "managed-agent-state-receipt-source",
                "permission": "deny",
                "flows": ["ingress"],
                "messages": ["reply", "declare_queryable"],
                "key_exprs": [format!("{namespace_prefix}/*/state/commit")],
            },
            {
                "id": "managed-agent-command-read",
                "permission": "allow",
                "flows": ["ingress"],
                "messages": ["declare_subscriber"],
                "key_exprs": [format!("{namespace_prefix}/*/commands/live/**")],
            },
            {
                "id": "managed-controller-command-publish",
                "permission": "allow",
                "flows": ["egress"],
                "messages": ["put"],
                "key_exprs": [format!("{namespace_prefix}/*/commands/live/**")],
            },
            {
                "id": "managed-agent-history-query",
                "permission": "allow",
                "flows": ["ingress"],
                "messages": ["query"],
                "key_exprs": [format!("{namespace_prefix}/*/commands/history")],
            },
            {
                "id": "managed-controller-history-reply",
                "permission": "allow",
                "flows": ["egress"],
                "messages": ["declare_queryable", "reply"],
                "key_exprs": [format!("{namespace_prefix}/*/commands/history")],
            },
        ]);
        access_control["subjects"] = json!([subject]);
        access_control["policies"] = json!([
            {
                "rules": [
                    "managed-agent-publications",
                    "managed-agent-presence",
                    "managed-controller-publication-subscription",
                    "managed-agent-state-commit",
                    "managed-agent-state-receipt",
                    "managed-agent-state-receipt-source",
                    "managed-agent-command-read",
                    "managed-controller-command-publish",
                    "managed-agent-history-query",
                    "managed-controller-history-reply",
                ],
                "subjects": ["managed-agents"],
            },
        ]);
    }
    insert_json5_value(config, "access_control", access_control)?;
    Ok(())
}

fn apply_endpoints(
    config: &mut Config,
    key: &'static str,
    kind: &'static str,
    endpoints: &[String],
) -> AppResult<()> {
    for endpoint in endpoints {
        EndPoint::from_str(endpoint).map_err(|_| {
            AppError::bad_request(format!("Invalid Zenoh {kind} endpoint: {endpoint}"))
        })?;
    }

    insert_json5_value(config, key, Value::from(endpoints.to_vec()))
}

fn insert_json5_serialized<T>(config: &mut Config, key: &'static str, value: T) -> AppResult<()>
where
    T: Serialize,
{
    let value = serde_json::to_value(value).map_err(|source| {
        AppError::internal(
            "zenoh_configuration_serialization",
            "Failed to serialize Zenoh session configuration.",
        )
        .with_source(source)
    })?;

    insert_json5_value(config, key, value)
}

fn insert_json5_value(config: &mut Config, key: &'static str, value: Value) -> AppResult<()> {
    config
        .insert_json5(key, &value.to_string())
        .map_err(|source| {
            AppError::internal(
                "zenoh_configuration",
                "Failed to apply Zenoh session configuration.",
            )
            .with_boxed_source(source)
        })?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{Config, configure_zenoh};
    use crate::config::{
        ZenohAccessControlConfig, ZenohAclPermission, ZenohAdminSpaceConfig, ZenohConfig,
        ZenohMode, ZenohTlsConfig,
    };

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn managed_acl_allows_agent_traffic_and_blocks_remote_authority() {
        use std::time::Duration;

        let key = "iot/v1/agents/agt_test/state/commit";
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("tcp/{}", listener.local_addr().unwrap());
        drop(listener);
        let mut router_config = Config::default();
        configure_zenoh(
            &mut router_config,
            &ZenohConfig {
                mode: ZenohMode::Router,
                listen_endpoints: vec![endpoint.clone()],
                access_control: ZenohAccessControlConfig {
                    enabled: true,
                    default_permission: ZenohAclPermission::Deny,
                    managed_gateway_namespace_prefix: Some("iot/v1/agents".into()),
                    managed_gateway_cert_common_names: vec![],
                },
                ..ZenohConfig::default()
            },
        )
        .unwrap();
        router_config
            .insert_json5("scouting/multicast/enabled", "false")
            .unwrap();
        let router = zenoh::open(router_config)
            .await
            .unwrap();
        let mut client_config = Config::default();
        client_config
            .insert_json5("mode", "\"client\"")
            .unwrap();
        client_config
            .insert_json5("scouting/multicast/enabled", "false")
            .unwrap();
        client_config
            .insert_json5("connect/endpoints", &serde_json::json!([endpoint]).to_string())
            .unwrap();
        let queryable = router
            .declare_queryable(key)
            .await
            .unwrap();
        let history_key = "iot/v1/agents/agt_test/commands/history";
        let history = router
            .declare_queryable("iot/v1/agents/*/commands/history")
            .await
            .unwrap();
        let publications = router
            .declare_subscriber("iot/v1/agents/*/**")
            .allowed_origin(zenoh::sample::Locality::Remote)
            .await
            .unwrap();
        let impostor = zenoh::open(client_config.clone())
            .await
            .unwrap();
        let impostor_queryable = impostor
            .declare_queryable(key)
            .await
            .unwrap();
        let client = zenoh::open(client_config)
            .await
            .unwrap();
        let replies = client
            .get(key)
            .payload("observation")
            .target(zenoh::query::QueryTarget::All)
            .timeout(Duration::from_secs(2))
            .await
            .unwrap();
        let query = tokio::time::timeout(Duration::from_secs(2), queryable.recv_async())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            query
                .payload()
                .unwrap()
                .try_to_string()
                .unwrap(),
            "observation"
        );
        query
            .reply(key, "stored")
            .await
            .unwrap();
        drop(query);
        let reply = tokio::time::timeout(Duration::from_secs(2), replies.recv_async())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            reply
                .result()
                .as_ref()
                .unwrap()
                .payload()
                .try_to_string()
                .unwrap(),
            "stored"
        );
        assert!(
            impostor_queryable
                .try_recv()
                .unwrap()
                .is_none()
        );

        let history_replies = client.get(history_key).await.unwrap();
        let history_query = tokio::time::timeout(Duration::from_secs(2), history.recv_async())
            .await
            .unwrap()
            .unwrap();
        history_query
            .reply(history_key, "history")
            .await
            .unwrap();
        drop(history_query);
        let history_reply =
            tokio::time::timeout(Duration::from_secs(2), history_replies.recv_async())
                .await
                .unwrap()
                .unwrap();
        assert_eq!(
            history_reply
                .result()
                .as_ref()
                .unwrap()
                .payload()
                .to_bytes()
                .as_ref(),
            b"history"
        );

        let command_key = "iot/v1/agents/agt_test/commands/live/cmd_test";
        let commands = client
            .declare_subscriber("iot/v1/agents/agt_test/commands/live/**")
            .await
            .unwrap();
        let publisher = router
            .declare_publisher(command_key)
            .allowed_destination(zenoh::sample::Locality::Remote)
            .await
            .unwrap();
        let matching = publisher
            .matching_listener()
            .await
            .unwrap();
        tokio::time::timeout(Duration::from_secs(2), async {
            while !publisher
                .matching_status()
                .await
                .unwrap()
                .matching()
            {
                matching.recv_async().await.unwrap();
            }
        })
        .await
        .unwrap();
        publisher.put("dispatch").await.unwrap();
        let command = tokio::time::timeout(Duration::from_secs(2), commands.recv_async())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(command.payload().to_bytes().as_ref(), b"dispatch");

        client
            .put("iot/v1/agents/agt_test/status/latest", "status")
            .await
            .unwrap();
        let status = tokio::time::timeout(Duration::from_secs(2), publications.recv_async())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(status.payload().to_bytes().as_ref(), b"status");

        impostor
            .put(command_key, "forged")
            .await
            .unwrap();
        assert!(
            tokio::time::timeout(Duration::from_millis(200), commands.recv_async())
                .await
                .is_err()
        );
        client.close().await.unwrap();
        impostor.close().await.unwrap();
        router.close().await.unwrap();
    }

    #[test]
    fn configure_zenoh_applies_mode() {
        let mut config = Config::default();
        let settings = ZenohConfig {
            enabled: true,
            mode: ZenohMode::Router,
            admin_space: ZenohAdminSpaceConfig { enabled: true, read: true, write: false },
            connect_endpoints: vec!["tcp/localhost:7448".into()],
            listen_endpoints: vec!["tcp/0.0.0.0:0".into()],
            ..ZenohConfig::default()
        };

        configure_zenoh(&mut config, &settings).expect("configuration should succeed");
        let serialized = serde_json::to_value(&config).expect("config should serialize");
        assert_eq!(serialized["mode"], serde_json::Value::String("router".into()));
        assert_eq!(serialized["adminspace"]["enabled"], serde_json::Value::Bool(true));
        assert_eq!(serialized["adminspace"]["permissions"]["read"], serde_json::Value::Bool(true));
        assert_eq!(
            serialized["adminspace"]["permissions"]["write"],
            serde_json::Value::Bool(false)
        );
    }

    #[test]
    fn configure_zenoh_rejects_invalid_endpoints() {
        let mut config = Config::default();
        let settings = ZenohConfig {
            connect_endpoints: vec!["not-an-endpoint".into()],
            ..ZenohConfig::default()
        };

        let error = configure_zenoh(&mut config, &settings).expect_err("configuration must fail");
        assert_eq!(error.code(), "bad_request");
    }

    #[test]
    fn configure_zenoh_applies_tls_and_access_control() {
        let mut config = Config::default();
        let settings = ZenohConfig {
            tls: ZenohTlsConfig {
                root_ca_certificate: Some("/etc/inari/ca.pem".into()),
                listen_private_key: Some("/etc/inari/router-key.pem".into()),
                listen_certificate: Some("/etc/inari/router.pem".into()),
                connect_private_key: Some("/etc/inari/client-key.pem".into()),
                connect_certificate: Some("/etc/inari/client.pem".into()),
                enable_mtls: true,
                close_link_on_expiration: true,
            },
            access_control: ZenohAccessControlConfig {
                enabled: true,
                default_permission: ZenohAclPermission::Deny,
                managed_gateway_namespace_prefix: Some("iot/v1/agents".into()),
                managed_gateway_cert_common_names: vec!["agt_test".into()],
            },
            ..ZenohConfig::default()
        };

        configure_zenoh(&mut config, &settings).expect("configuration should succeed");

        let serialized = serde_json::to_value(&config).expect("config should serialize");
        assert_eq!(
            serialized["transport"]["link"]["tls"]["root_ca_certificate"],
            serde_json::Value::String("/etc/inari/ca.pem".into())
        );
        assert_eq!(
            serialized["transport"]["link"]["tls"]["connect_private_key"],
            serde_json::Value::String("/etc/inari/client-key.pem".into())
        );
        assert_eq!(
            serialized["transport"]["link"]["tls"]["connect_certificate"],
            serde_json::Value::String("/etc/inari/client.pem".into())
        );
        assert_eq!(
            serialized["transport"]["link"]["tls"]["listen_private_key"],
            serde_json::Value::String("/etc/inari/router-key.pem".into())
        );
        assert_eq!(
            serialized["transport"]["link"]["tls"]["listen_certificate"],
            serde_json::Value::String("/etc/inari/router.pem".into())
        );
        assert_eq!(
            serialized["transport"]["link"]["tls"]["enable_mtls"],
            serde_json::Value::Bool(true)
        );
        assert_eq!(
            serialized["transport"]["link"]["tls"]["close_link_on_expiration"],
            serde_json::Value::Bool(true)
        );
        assert_eq!(serialized["access_control"]["enabled"], serde_json::Value::Bool(true));
        assert_eq!(
            serialized["access_control"]["default_permission"],
            serde_json::Value::String("deny".into())
        );
        assert_eq!(
            serialized["access_control"]["subjects"][0]["cert_common_names"][0],
            serde_json::Value::String("agt_test".into())
        );
        assert_eq!(
            serialized["access_control"]["rules"][0]["key_exprs"][1],
            serde_json::Value::String("iot/v1/agents/*/status/latest".into())
        );
    }
}
