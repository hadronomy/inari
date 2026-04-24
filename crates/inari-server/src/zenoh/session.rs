use std::str::FromStr;

use ::zenoh::{
    bytes::{Encoding, ZBytes},
    config::EndPoint,
};
use bytes::Bytes;
use serde::Serialize;
use serde_json::Value;

use crate::{
    config::ZenohConfig,
    error::{AppError, AppResult},
};

use super::KeyExpression;

pub(super) async fn open_session(config: &ZenohConfig) -> AppResult<::zenoh::Session> {
    let mut zenoh_config = ::zenoh::Config::default();
    configure_zenoh(&mut zenoh_config, config)?;

    ::zenoh::open(zenoh_config).await.map_err(|source| {
        AppError::service_unavailable(format!("Failed to open the Zenoh session: {source}"))
    })
}

pub(super) async fn close_session(session: ::zenoh::Session) {
    if let Err(source) = session.close().await {
        tracing::warn!(error = %source, "failed to close Zenoh session cleanly");
    }
}

pub(super) async fn publish(
    session: &::zenoh::Session,
    key: &KeyExpression,
    payload: Bytes,
    encoding: Encoding,
) -> AppResult<()> {
    let payload = ZBytes::from(payload);
    session
        .put(key, payload)
        .encoding(encoding)
        .await
        .map_err(|_| AppError::service_unavailable("Zenoh publish failed."))?;

    Ok(())
}

pub(super) async fn delete(session: &::zenoh::Session, key: &KeyExpression) -> AppResult<()> {
    session.delete(key).await.map_err(|_| AppError::service_unavailable("Zenoh delete failed."))?;

    Ok(())
}

fn configure_zenoh(config: &mut ::zenoh::Config, settings: &ZenohConfig) -> AppResult<()> {
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

    Ok(())
}

fn apply_endpoints(
    config: &mut ::zenoh::Config,
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

fn insert_json5_serialized<T>(
    config: &mut ::zenoh::Config,
    key: &'static str,
    value: T,
) -> AppResult<()>
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

fn insert_json5_value(
    config: &mut ::zenoh::Config,
    key: &'static str,
    value: Value,
) -> AppResult<()> {
    config.insert_json5(key, &value.to_string()).map_err(|source| {
        AppError::internal("zenoh_configuration", "Failed to apply Zenoh session configuration.")
            .with_boxed_source(source)
    })?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::configure_zenoh;
    use crate::config::{ZenohAdminSpaceConfig, ZenohConfig, ZenohMode};

    #[test]
    fn configure_zenoh_applies_mode() {
        let mut config = ::zenoh::Config::default();
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
        let mut config = ::zenoh::Config::default();
        let settings = ZenohConfig {
            connect_endpoints: vec!["not-an-endpoint".into()],
            ..ZenohConfig::default()
        };

        let error = configure_zenoh(&mut config, &settings).expect_err("configuration must fail");
        assert_eq!(error.code(), "bad_request");
    }
}
