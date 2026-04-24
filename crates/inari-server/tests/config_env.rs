use std::collections::HashMap;
use std::num::NonZeroUsize;
use std::time::Duration;

use inari_server::{LoadedConfig, LogFormat, ZenohMode};

#[test]
fn environment_overrides_cover_every_nested_field() {
    let loaded = LoadedConfig::load_from_environment_map(HashMap::from([
        ("INARI_SERVER_SERVER__BIND".into(), "127.0.0.1:9000".into()),
        ("INARI_SERVER_SERVER__REQUEST_TIMEOUT".into(), "45s".into()),
        ("INARI_SERVER_SERVER__SHUTDOWN_GRACE_PERIOD".into(), "1min".into()),
        ("INARI_SERVER_SERVER__MAX_BODY_SIZE_BYTES".into(), "4096".into()),
        ("INARI_SERVER_RUNTIME__WORKER_THREADS".into(), "8".into()),
        ("INARI_SERVER_RUNTIME__MAX_BLOCKING_THREADS".into(), "64".into()),
        ("INARI_SERVER_RUNTIME__THREAD_STACK_SIZE_BYTES".into(), "4194304".into()),
        ("INARI_SERVER_RUNTIME__EVENT_INTERVAL".into(), "99".into()),
        ("INARI_SERVER_RUNTIME__GLOBAL_QUEUE_INTERVAL".into(), "17".into()),
        ("INARI_SERVER_RUNTIME__THREAD_KEEP_ALIVE".into(), "15s".into()),
        ("INARI_SERVER_OBSERVABILITY__SERVICE_NAME".into(), "test-service".into()),
        ("INARI_SERVER_OBSERVABILITY__FILTER".into(), "info".into()),
        ("INARI_SERVER_OBSERVABILITY__FORMAT".into(), "json".into()),
        ("INARI_SERVER_OBSERVABILITY__INCLUDE_TARGETS".into(), "false".into()),
        ("INARI_SERVER_OBSERVABILITY__INCLUDE_THREAD_IDS".into(), "true".into()),
        ("INARI_SERVER_OBSERVABILITY__INCLUDE_THREAD_NAMES".into(), "false".into()),
        ("INARI_SERVER_HTTP__CORS__ENABLED".into(), "true".into()),
        (
            "INARI_SERVER_HTTP__CORS__ALLOW_ORIGINS".into(),
            "https://a.example,https://b.example".into(),
        ),
        ("INARI_SERVER_HTTP__CORS__ALLOW_METHODS".into(), "GET,PUT".into()),
        ("INARI_SERVER_HTTP__CORS__ALLOW_HEADERS".into(), "authorization,x-request-id".into()),
        ("INARI_SERVER_HTTP__CORS__EXPOSE_HEADERS".into(), "x-request-id,x-trace-id".into()),
        ("INARI_SERVER_HTTP__CORS__ALLOW_CREDENTIALS".into(), "true".into()),
        ("INARI_SERVER_HTTP__CORS__MAX_AGE".into(), "5min".into()),
        ("INARI_SERVER_HTTP__ZENOH_REST__ENABLED".into(), "true".into()),
        ("INARI_SERVER_HTTP__ZENOH_REST__ALLOW_ADMIN_SPACE".into(), "true".into()),
        ("INARI_SERVER_HTTP__ZENOH_REST__QUERY_TIMEOUT".into(), "11s".into()),
        ("INARI_SERVER_HTTP__ZENOH_REST__SSE_KEEP_ALIVE".into(), "20s".into()),
        ("INARI_SERVER_HTTP__ZENOH_REST__SSE_BUFFER".into(), "128".into()),
        ("INARI_SERVER_ZENOH__ENABLED".into(), "true".into()),
        ("INARI_SERVER_ZENOH__MODE".into(), "router".into()),
        ("INARI_SERVER_ZENOH__ADMIN_SPACE__ENABLED".into(), "true".into()),
        ("INARI_SERVER_ZENOH__ADMIN_SPACE__READ".into(), "true".into()),
        ("INARI_SERVER_ZENOH__ADMIN_SPACE__WRITE".into(), "false".into()),
        (
            "INARI_SERVER_ZENOH__CONNECT_ENDPOINTS".into(),
            "tcp/127.0.0.1:7447,udp/127.0.0.1:7448".into(),
        ),
        ("INARI_SERVER_ZENOH__LISTEN_ENDPOINTS".into(), "tcp/0.0.0.0:7447".into()),
        ("INARI_SERVER_ZENOH__RETRY_INTERVAL".into(), "7s".into()),
        ("INARI_SERVER_ZENOH__COMMAND_BUFFER".into(), "512".into()),
        ("INARI_SERVER_ZENOH__EVENT_BUFFER".into(), "256".into()),
        ("INARI_SERVER_PROTOCOL__NAMESPACE".into(), "inari/test".into()),
        ("INARI_SERVER_PROTOCOL__MAX_CONCURRENT_REQUESTS".into(), "2048".into()),
    ]))
    .expect("environment overrides should deserialize");

    assert!(loaded.origin.includes_environment);
    assert_eq!(loaded.settings.server.bind, "127.0.0.1:9000".parse().unwrap());
    assert_eq!(loaded.settings.server.request_timeout, Duration::from_secs(45));
    assert_eq!(
        loaded
            .settings
            .server
            .shutdown_grace_period,
        Duration::from_secs(60)
    );
    assert_eq!(
        loaded
            .settings
            .server
            .max_body_size_bytes,
        4096
    );
    assert_eq!(
        loaded
            .settings
            .runtime
            .worker_threads
            .map(NonZeroUsize::get),
        Some(8)
    );
    assert_eq!(
        loaded
            .settings
            .runtime
            .max_blocking_threads,
        64
    );
    assert_eq!(
        loaded
            .settings
            .runtime
            .thread_stack_size_bytes,
        4_194_304
    );
    assert_eq!(loaded.settings.runtime.event_interval, 99);
    assert_eq!(
        loaded
            .settings
            .runtime
            .global_queue_interval,
        17
    );
    assert_eq!(
        loaded
            .settings
            .runtime
            .thread_keep_alive,
        Duration::from_secs(15)
    );
    assert_eq!(
        loaded
            .settings
            .observability
            .service_name,
        "test-service"
    );
    assert_eq!(loaded.settings.observability.filter, "info");
    assert_eq!(loaded.settings.observability.format, LogFormat::Json);
    assert!(
        !loaded
            .settings
            .observability
            .include_targets
    );
    assert!(
        loaded
            .settings
            .observability
            .include_thread_ids
    );
    assert!(
        !loaded
            .settings
            .observability
            .include_thread_names
    );
    assert!(loaded.settings.http.cors.enabled);
    assert_eq!(
        loaded.settings.http.cors.allow_origins,
        vec!["https://a.example", "https://b.example"]
    );
    assert_eq!(loaded.settings.http.cors.allow_methods, vec!["GET", "PUT"]);
    assert_eq!(loaded.settings.http.cors.allow_headers, vec!["authorization", "x-request-id"]);
    assert_eq!(loaded.settings.http.cors.expose_headers, vec!["x-request-id", "x-trace-id"]);
    assert!(
        loaded
            .settings
            .http
            .cors
            .allow_credentials
    );
    assert_eq!(loaded.settings.http.cors.max_age, Duration::from_secs(300));
    assert!(loaded.settings.http.zenoh_rest.enabled);
    assert!(
        loaded
            .settings
            .http
            .zenoh_rest
            .allow_admin_space
    );
    assert_eq!(
        loaded
            .settings
            .http
            .zenoh_rest
            .query_timeout,
        Duration::from_secs(11)
    );
    assert_eq!(
        loaded
            .settings
            .http
            .zenoh_rest
            .sse_keep_alive,
        Duration::from_secs(20)
    );
    assert_eq!(
        loaded
            .settings
            .http
            .zenoh_rest
            .sse_buffer,
        128
    );
    assert!(loaded.settings.zenoh.enabled);
    assert_eq!(loaded.settings.zenoh.mode, ZenohMode::Router);
    assert!(
        loaded
            .settings
            .zenoh
            .admin_space
            .enabled
    );
    assert!(loaded.settings.zenoh.admin_space.read);
    assert!(!loaded.settings.zenoh.admin_space.write);
    assert_eq!(
        loaded.settings.zenoh.connect_endpoints,
        vec!["tcp/127.0.0.1:7447", "udp/127.0.0.1:7448"]
    );
    assert_eq!(loaded.settings.zenoh.listen_endpoints, vec!["tcp/0.0.0.0:7447"]);
    assert_eq!(loaded.settings.zenoh.retry_interval, Duration::from_secs(7));
    assert_eq!(loaded.settings.zenoh.command_buffer, 512);
    assert_eq!(loaded.settings.zenoh.event_buffer, 256);
    assert_eq!(loaded.settings.protocol.namespace, "inari/test");
    assert_eq!(
        loaded
            .settings
            .protocol
            .max_concurrent_requests,
        2048
    );
}

#[test]
fn environment_rejects_non_router_zenoh_modes() {
    let result = LoadedConfig::load_from_environment_map(HashMap::from([(
        "INARI_SERVER_ZENOH__MODE".into(),
        "peer".into(),
    )]));

    assert!(result.is_err(), "server config should reject non-router Zenoh modes");
}
