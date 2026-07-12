use std::collections::HashMap;
use std::env;
use std::net::{Ipv4Addr, SocketAddr};
use std::num::NonZeroUsize;
use std::path::PathBuf;
use std::time::Duration;

use config as config_rs;
use config_rs::{Config, Environment, File, FileFormat};
use serde::{Deserialize, Serialize};

use crate::coordination::{ChannelCapacity, ConcurrencyLimit};
use crate::error::ConfigError;

mod identity;
mod managed_gateway;
mod platform;

pub use self::identity::{IdentityConfig, OidcConfig};
pub use self::managed_gateway::{
    ManagedGatewayCertificateConfig, ManagedGatewayCertificateMode, ManagedGatewayConfig,
    ManagedGatewayDataPlaneConfig, ManagedGatewayOnboardingConfig, StepCaSigningAlgorithm,
};
pub use self::platform::{DatabaseConfig, OrganizationConfig};

const CONFIG_PATH_ENV: &str = "INARI_SERVER_CONFIG";
pub const CONFIG_VERSION: u32 = 1;
const ENV_PREFIX: &str = "INARI_SERVER";
const ENV_SEPARATOR: &str = "__";
const DEFAULT_CONFIG_CANDIDATES: &[&str] = &["inari-server.toml", "config/inari-server.toml"];
const ENV_LIST_KEYS: &[&str] = &[
    "http.cors.allow_origins",
    "http.cors.allow_methods",
    "http.cors.allow_headers",
    "http.cors.expose_headers",
    "identity.oidc.scopes",
    "managed_gateway.controller_actions",
    "managed_gateway.supported_protocol_versions",
    "managed_gateway.data_plane.connect_endpoints",
    "managed_gateway.certificate.step_ca_authorized_sans",
    "zenoh.access_control.managed_gateway_cert_common_names",
    "zenoh.connect_endpoints",
    "zenoh.listen_endpoints",
];
const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const DEFAULT_SHUTDOWN_GRACE_PERIOD: Duration = Duration::from_secs(30);
const DEFAULT_THREAD_KEEP_ALIVE: Duration = Duration::from_secs(10);
const DEFAULT_CORS_MAX_AGE: Duration = Duration::from_mins(10);
const DEFAULT_ZENOH_REST_QUERY_TIMEOUT: Duration = Duration::from_secs(15);
const DEFAULT_ZENOH_REST_SSE_KEEP_ALIVE: Duration = Duration::from_secs(15);
const DEFAULT_ZENOH_RETRY_INTERVAL: Duration = Duration::from_secs(5);
const DEFAULT_ZENOH_REST_SSE_BUFFER: usize = 64;
const DEFAULT_ZENOH_COMMAND_BUFFER: usize = 256;
const DEFAULT_ZENOH_EVENT_BUFFER: usize = 128;
const DEFAULT_ZENOH_REST_MAX_CONCURRENT_QUERIES: usize = 64;
const DEFAULT_INARI_API_MAX_CONCURRENT_REQUESTS: usize = 1024;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LoadedConfig {
    pub settings: AppConfig,
    pub origin: ConfigOrigin,
}

impl LoadedConfig {
    pub fn load() -> Result<Self, ConfigError> {
        let origin = ConfigOrigin::discover()?;
        let settings = build_settings(&origin.files, environment_source())?;

        Ok(Self { settings, origin })
    }

    #[doc(hidden)]
    pub fn load_from_environment_map(
        overrides: HashMap<String, String>,
    ) -> Result<Self, ConfigError> {
        let origin = ConfigOrigin {
            files: config_files(None)?,
            includes_environment: !overrides.is_empty(),
        };
        let settings = build_settings(&origin.files, environment_source_with(overrides))?;

        Ok(Self { settings, origin })
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ConfigOrigin {
    pub files: Vec<PathBuf>,
    pub includes_environment: bool,
}

impl ConfigOrigin {
    fn discover() -> Result<Self, ConfigError> {
        let explicit_path = env::var_os(CONFIG_PATH_ENV).map(PathBuf::from);

        Ok(Self {
            files: config_files(explicit_path)?,
            includes_environment: env::vars_os()
                .map(|(key, _)| key)
                .filter_map(|key| key.into_string().ok())
                .any(|key| is_environment_override_key(&key)),
        })
    }
}

impl std::fmt::Display for ConfigOrigin {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match (self.files.as_slice(), self.includes_environment) {
            ([], true) => f.write_str("defaults + environment"),
            ([], false) => f.write_str("defaults"),
            ([file], true) => write!(f, "{} + environment", file.display()),
            ([file], false) => write!(f, "{}", file.display()),
            (files, includes_environment) => {
                let files = files
                    .iter()
                    .map(|path| path.display().to_string())
                    .collect::<Vec<_>>()
                    .join(", ");

                if includes_environment {
                    write!(f, "{files} + environment")
                } else {
                    f.write_str(&files)
                }
            },
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct AppConfig {
    pub config_version: u32,
    pub server: ServerConfig,
    pub database: DatabaseConfig,
    pub organization: OrganizationConfig,
    pub runtime: RuntimeConfig,
    pub observability: ObservabilityConfig,
    pub identity: IdentityConfig,
    pub http: HttpConfig,
    pub managed_gateway: ManagedGatewayConfig,
    pub zenoh: ZenohConfig,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            config_version: CONFIG_VERSION,
            server: ServerConfig::default(),
            database: DatabaseConfig::default(),
            organization: OrganizationConfig::default(),
            runtime: RuntimeConfig::default(),
            observability: ObservabilityConfig::default(),
            identity: IdentityConfig::default(),
            http: HttpConfig::default(),
            managed_gateway: ManagedGatewayConfig::default(),
            zenoh: ZenohConfig::default(),
        }
    }
}

impl AppConfig {
    fn validate(self) -> Result<Self, ConfigError> {
        if self.config_version != CONFIG_VERSION {
            return Err(ConfigError::invalid(format!(
                "Unsupported config_version {}; expected {CONFIG_VERSION}.",
                self.config_version
            )));
        }
        self.managed_gateway.validate()?;
        self.identity.validate(&self.server)?;
        self.database
            .validate(self.managed_gateway.enabled || self.identity.oidc.enabled)?;
        self.organization.validate()?;
        if self.identity.oidc.enabled && !self.managed_gateway.enabled {
            return Err(ConfigError::invalid(
                "managed_gateway.enabled must be true when OIDC is enabled because sessions require the controller database.",
            ));
        }
        if self.server.production && self.managed_gateway.enabled && !self.identity.oidc.enabled {
            return Err(ConfigError::invalid(
                "identity.oidc.enabled must be true for a production managed controller.",
            ));
        }
        if self.managed_gateway.enabled && !self.zenoh.enabled {
            return Err(ConfigError::invalid(
                "zenoh.enabled must be true when the managed gateway is enabled.",
            ));
        }
        if self.http.zenoh_rest.enabled && !self.identity.oidc.enabled {
            return Err(ConfigError::invalid(
                "identity.oidc.enabled must be true when the direct Zenoh HTTP surface is enabled.",
            ));
        }
        if self.server.production && self.zenoh.mode != ZenohMode::Client {
            return Err(ConfigError::invalid(
                "production controllers must connect to a separate Zenoh router in client mode.",
            ));
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    pub bind: SocketAddr,
    pub public_url: Option<url::Url>,
    pub production: bool,
    #[serde(with = "humantime_serde")]
    pub request_timeout: Duration,
    #[serde(with = "humantime_serde")]
    pub shutdown_grace_period: Duration,
    pub max_body_size_bytes: usize,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            bind: (Ipv4Addr::LOCALHOST, 8080).into(),
            public_url: None,
            production: false,
            request_timeout: DEFAULT_REQUEST_TIMEOUT,
            shutdown_grace_period: DEFAULT_SHUTDOWN_GRACE_PERIOD,
            max_body_size_bytes: 1024 * 1024,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct RuntimeConfig {
    pub worker_threads: Option<NonZeroUsize>,
    pub max_blocking_threads: usize,
    pub thread_stack_size_bytes: usize,
    pub event_interval: u32,
    pub global_queue_interval: u32,
    #[serde(with = "humantime_serde")]
    pub thread_keep_alive: Duration,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            worker_threads: None,
            max_blocking_threads: 512,
            thread_stack_size_bytes: 2 * 1024 * 1024,
            event_interval: 61,
            global_queue_interval: 31,
            thread_keep_alive: DEFAULT_THREAD_KEEP_ALIVE,
        }
    }
}

impl RuntimeConfig {
    #[must_use]
    pub fn worker_threads(&self) -> usize {
        self.worker_threads
            .map(NonZeroUsize::get)
            .or_else(|| {
                std::thread::available_parallelism()
                    .ok()
                    .map(NonZeroUsize::get)
            })
            .unwrap_or(4)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ObservabilityConfig {
    pub service_name: String,
    pub filter: String,
    pub format: LogFormat,
    pub include_targets: bool,
    pub include_thread_ids: bool,
    pub include_thread_names: bool,
}

impl Default for ObservabilityConfig {
    fn default() -> Self {
        Self {
            service_name: "inari-server".into(),
            filter: "inari_server=debug,tower_http=info,axum=info".into(),
            format: LogFormat::Pretty,
            include_targets: true,
            include_thread_ids: false,
            include_thread_names: false,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LogFormat {
    #[default]
    Pretty,
    Json,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct HttpConfig {
    pub cors: CorsConfig,
    pub inari_api: InariApiConfig,
    pub zenoh_rest: ZenohRestConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct InariApiConfig {
    pub max_concurrent_requests: ConcurrencyLimit,
}

impl Default for InariApiConfig {
    fn default() -> Self {
        Self { max_concurrent_requests: default_inari_api_max_concurrent_requests() }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct CorsConfig {
    pub enabled: bool,
    pub allow_origins: Vec<String>,
    pub allow_methods: Vec<String>,
    pub allow_headers: Vec<String>,
    pub expose_headers: Vec<String>,
    pub allow_credentials: bool,
    #[serde(with = "humantime_serde")]
    pub max_age: Duration,
}

impl Default for CorsConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            allow_origins: Vec::new(),
            allow_methods: ["GET", "HEAD", "POST", "OPTIONS"]
                .map(String::from)
                .to_vec(),
            allow_headers: ["authorization", "content-type", "x-request-id"]
                .map(String::from)
                .to_vec(),
            expose_headers: ["x-request-id"]
                .map(String::from)
                .to_vec(),
            allow_credentials: false,
            max_age: DEFAULT_CORS_MAX_AGE,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ZenohRestConfig {
    pub enabled: bool,
    pub allow_admin_space: bool,
    #[serde(with = "humantime_serde")]
    pub query_timeout: Duration,
    #[serde(with = "humantime_serde")]
    pub sse_keep_alive: Duration,
    pub sse_buffer: ChannelCapacity,
    pub max_concurrent_requests: ConcurrencyLimit,
}

impl Default for ZenohRestConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            allow_admin_space: false,
            query_timeout: DEFAULT_ZENOH_REST_QUERY_TIMEOUT,
            sse_keep_alive: DEFAULT_ZENOH_REST_SSE_KEEP_ALIVE,
            sse_buffer: default_zenoh_rest_sse_buffer(),
            max_concurrent_requests: default_zenoh_rest_max_concurrent_requests(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ZenohConfig {
    pub enabled: bool,
    pub mode: ZenohMode,
    pub admin_space: ZenohAdminSpaceConfig,
    pub connect_endpoints: Vec<String>,
    pub listen_endpoints: Vec<String>,
    pub tls: ZenohTlsConfig,
    pub access_control: ZenohAccessControlConfig,
    #[serde(with = "humantime_serde")]
    pub open_retry_interval: Duration,
    pub command_buffer: ChannelCapacity,
    pub event_buffer: ChannelCapacity,
}

impl Default for ZenohConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            mode: ZenohMode::Client,
            admin_space: ZenohAdminSpaceConfig::default(),
            connect_endpoints: Vec::new(),
            listen_endpoints: Vec::new(),
            tls: ZenohTlsConfig::default(),
            access_control: ZenohAccessControlConfig::default(),
            open_retry_interval: DEFAULT_ZENOH_RETRY_INTERVAL,
            command_buffer: default_zenoh_command_buffer(),
            event_buffer: default_zenoh_event_buffer(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ZenohAdminSpaceConfig {
    pub enabled: bool,
    pub read: bool,
    pub write: bool,
}

impl Default for ZenohAdminSpaceConfig {
    fn default() -> Self {
        Self { enabled: false, read: true, write: false }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ZenohMode {
    #[default]
    Client,
    Router,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ZenohTlsConfig {
    pub root_ca_certificate: Option<PathBuf>,
    pub listen_certificate: Option<PathBuf>,
    pub listen_private_key: Option<PathBuf>,
    pub connect_certificate: Option<PathBuf>,
    pub connect_private_key: Option<PathBuf>,
    pub enable_mtls: bool,
    pub close_link_on_expiration: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ZenohAccessControlConfig {
    pub enabled: bool,
    pub default_permission: ZenohAclPermission,
    pub managed_gateway_namespace_prefix: Option<String>,
    pub managed_gateway_cert_common_names: Vec<String>,
}

impl Default for ZenohAccessControlConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            default_permission: ZenohAclPermission::Deny,
            managed_gateway_namespace_prefix: None,
            managed_gateway_cert_common_names: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ZenohAclPermission {
    Allow,
    #[default]
    Deny,
}

fn default_zenoh_rest_max_concurrent_requests() -> ConcurrencyLimit {
    DEFAULT_ZENOH_REST_MAX_CONCURRENT_QUERIES
        .try_into()
        .expect("default Zenoh REST concurrency limit must be valid")
}

fn default_zenoh_rest_sse_buffer() -> ChannelCapacity {
    DEFAULT_ZENOH_REST_SSE_BUFFER
        .try_into()
        .expect("default Zenoh REST SSE buffer must be valid")
}

fn default_zenoh_command_buffer() -> ChannelCapacity {
    DEFAULT_ZENOH_COMMAND_BUFFER
        .try_into()
        .expect("default Zenoh command buffer must be valid")
}

fn default_zenoh_event_buffer() -> ChannelCapacity {
    DEFAULT_ZENOH_EVENT_BUFFER
        .try_into()
        .expect("default Zenoh event buffer must be valid")
}

fn default_inari_api_max_concurrent_requests() -> ConcurrencyLimit {
    DEFAULT_INARI_API_MAX_CONCURRENT_REQUESTS
        .try_into()
        .expect("default protocol concurrency limit must be valid")
}

fn build_settings(files: &[PathBuf], environment: Environment) -> Result<AppConfig, ConfigError> {
    let defaults = toml::to_string(&AppConfig::default())
        .map_err(|source| ConfigError::SerializeDefaults { source })?;
    let mut builder = Config::builder().add_source(File::from_str(&defaults, FileFormat::Toml));

    for path in files {
        builder = builder.add_source(File::from(path.clone()));
    }

    let config = builder
        .add_source(environment)
        .build()
        .map_err(|source| ConfigError::Build { source })?;

    config
        .try_deserialize()
        .map_err(|source| ConfigError::Deserialize { source })
        .and_then(AppConfig::validate)
}

fn config_files(explicit_path: Option<PathBuf>) -> Result<Vec<PathBuf>, ConfigError> {
    match explicit_path {
        Some(path) => {
            if !path.exists() {
                return Err(ConfigError::MissingExplicitPath { key: CONFIG_PATH_ENV, path });
            }

            Ok(vec![path])
        },
        None => Ok(DEFAULT_CONFIG_CANDIDATES
            .iter()
            .map(PathBuf::from)
            .filter(|path| path.exists())
            .collect()),
    }
}

fn environment_source() -> Environment {
    build_environment_source()
}

fn environment_source_with(source: HashMap<String, String>) -> Environment {
    build_environment_source().source(Some(source))
}

fn build_environment_source() -> Environment {
    ENV_LIST_KEYS.iter().fold(
        Environment::with_prefix(ENV_PREFIX)
            .prefix_separator("_")
            .separator(ENV_SEPARATOR)
            .ignore_empty(true)
            .list_separator(",")
            .try_parsing(true),
        |source, key| source.with_list_parse_key(key),
    )
}

fn is_environment_override_key(key: &str) -> bool {
    key.starts_with(&format!("{ENV_PREFIX}_")) && key != CONFIG_PATH_ENV
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{AppConfig, LoadedConfig};
    use crate::{ChannelCapacity, ConcurrencyLimit};

    #[test]
    fn default_loaded_config_has_defaults_origin() {
        let loaded = LoadedConfig::default();

        assert_eq!(loaded.origin.to_string(), "defaults");
        assert_eq!(loaded.settings, AppConfig::default());
    }

    #[test]
    fn config_accepts_human_readable_durations() {
        let config: AppConfig = toml::from_str(
            r#"
                [server]
                request_timeout = "45s"
                shutdown_grace_period = "1min"

                [runtime]
                thread_keep_alive = "15s"

                [http.cors]
                max_age = "10min"

                [http.zenoh_rest]
                query_timeout = "12s"
                sse_keep_alive = "20s"

                [zenoh]
                retry_interval = "5s"
            "#,
        )
        .expect("configuration should parse");

        assert_eq!(config.server.request_timeout, Duration::from_secs(45));
        assert_eq!(config.server.shutdown_grace_period, Duration::from_secs(60));
        assert_eq!(config.runtime.thread_keep_alive, Duration::from_secs(15));
        assert_eq!(config.http.cors.max_age, Duration::from_secs(600));
        assert_eq!(config.http.zenoh_rest.query_timeout, Duration::from_secs(12));
        assert_eq!(config.http.zenoh_rest.sse_keep_alive, Duration::from_secs(20));
        assert_eq!(config.zenoh.open_retry_interval, Duration::from_secs(5));
    }

    #[test]
    fn config_rejects_zero_concurrency_limits() {
        let result = toml::from_str::<AppConfig>(
            r#"
                [http.inari_api]
                max_concurrent_requests = 0
            "#,
        );

        assert!(result.is_err(), "zero concurrency limits should fail during config parsing");
    }

    #[test]
    fn config_rejects_concurrency_limits_above_the_semaphore_maximum() {
        let result = toml::from_str::<AppConfig>(&format!(
            r#"
                [http.inari_api]
                max_concurrent_requests = {}
            "#,
            ConcurrencyLimit::MAX + 1
        ));

        assert!(
            result.is_err(),
            "concurrency limits above the semaphore maximum should fail during config parsing"
        );
    }

    #[test]
    fn config_rejects_zero_channel_capacities() {
        let result = toml::from_str::<AppConfig>(
            r#"
                [http.zenoh_rest]
                sse_buffer = 0
            "#,
        );

        assert!(result.is_err(), "zero channel capacities should fail during config parsing");
    }

    #[test]
    fn config_accepts_typed_channel_capacities() {
        let config: AppConfig = toml::from_str(
            r#"
                [http.zenoh_rest]
                sse_buffer = 32

                [zenoh]
                command_buffer = 48
                event_buffer = 24
            "#,
        )
        .expect("configuration should parse");

        assert_eq!(usize::from(config.http.zenoh_rest.sse_buffer), 32);
        assert_eq!(usize::from(config.zenoh.command_buffer), 48);
        assert_eq!(usize::from(config.zenoh.event_buffer), 24);
        assert_eq!(
            ChannelCapacity::try_from(24).expect("non-zero capacity should be valid"),
            config.zenoh.event_buffer
        );
    }
}
