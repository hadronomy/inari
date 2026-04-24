#![forbid(unsafe_code)]

pub mod app;
pub mod config;
pub mod error;
pub mod http;
pub mod observability;
pub mod protocol;
pub mod runtime;
pub mod shutdown;
pub mod state;
pub mod zenoh;

pub use app::{ServerApplication, ServerBuilder};
pub use config::{
    AppConfig, ConfigOrigin, CorsConfig, HttpConfig, LoadedConfig, LogFormat, ObservabilityConfig,
    ProtocolConfig, RuntimeConfig, ServerConfig, ZenohAdminSpaceConfig, ZenohConfig, ZenohMode,
    ZenohRestConfig,
};
pub use error::{AppError, AppResult, ConfigError};
pub use observability::init as init_observability;
pub use protocol::{NoopProtocolModule, ProtocolDescriptor, ProtocolModule, ProtocolStage};
pub use runtime::build_runtime;
pub use state::{AppState, ComponentReadiness, ReadinessLevel, ReadinessSnapshot};
pub use zenoh::{
    KeyExpression, ZenohConnectionState, ZenohEvent, ZenohHandle, ZenohStatus, ZenohSupervisor,
};
