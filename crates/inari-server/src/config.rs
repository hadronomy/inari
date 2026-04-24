use std::collections::HashMap;
use std::env;
use std::net::{Ipv4Addr, SocketAddr};
use std::num::NonZeroUsize;
use std::path::PathBuf;
use std::time::Duration;

use config as config_rs;
use config_rs::{Config, Environment, File, FileFormat};
use serde::{Deserialize, Serialize};

use crate::error::ConfigError;

const CONFIG_PATH_ENV: &str = "INARI_SERVER_CONFIG";
const ENV_PREFIX: &str = "INARI_SERVER";
const ENV_SEPARATOR: &str = "__";
const DEFAULT_CONFIG_CANDIDATES: &[&str] = &["inari-server.toml", "config/inari-server.toml"];
const ENV_LIST_KEYS: &[&str] = &[
    "http.cors.allow_origins",
    "http.cors.allow_methods",
    "http.cors.allow_headers",
    "http.cors.expose_headers",
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

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    pub server: ServerConfig,
    pub runtime: RuntimeConfig,
    pub observability: ObservabilityConfig,
    pub http: HttpConfig,
    pub zenoh: ZenohConfig,
    pub protocol: ProtocolConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ServerConfig {
    pub bind: SocketAddr,
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
    pub zenoh_rest: ZenohRestConfig,
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
    pub sse_buffer: usize,
}

impl Default for ZenohRestConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            allow_admin_space: false,
            query_timeout: DEFAULT_ZENOH_REST_QUERY_TIMEOUT,
            sse_keep_alive: DEFAULT_ZENOH_REST_SSE_KEEP_ALIVE,
            sse_buffer: 64,
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
    #[serde(with = "humantime_serde")]
    pub retry_interval: Duration,
    pub command_buffer: usize,
    pub event_buffer: usize,
}

impl Default for ZenohConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            mode: ZenohMode::Router,
            admin_space: ZenohAdminSpaceConfig::default(),
            connect_endpoints: Vec::new(),
            listen_endpoints: Vec::new(),
            retry_interval: DEFAULT_ZENOH_RETRY_INTERVAL,
            command_buffer: 256,
            event_buffer: 128,
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
    Router,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ProtocolConfig {
    pub namespace: String,
    pub max_concurrent_requests: usize,
}

impl Default for ProtocolConfig {
    fn default() -> Self {
        Self { namespace: "inari".into(), max_concurrent_requests: 1024 }
    }
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
        assert_eq!(config.zenoh.retry_interval, Duration::from_secs(5));
    }
}
