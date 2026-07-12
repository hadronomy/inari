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
                "id": "managed-agent-command-read",
                "permission": "allow",
                "messages": ["declare_subscriber", "query", "reply"],
                "key_exprs": [
                    format!("{namespace_prefix}/*/commands/live/**"),
                    format!("{namespace_prefix}/*/commands/history"),
                ],
            },
        ]);
        access_control["subjects"] = json!([subject]);
        access_control["policies"] = json!([
            {
                "rules": [
                    "managed-agent-publications",
                    "managed-agent-command-read",
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
