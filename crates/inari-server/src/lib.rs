#![forbid(unsafe_code)]

pub mod coordination;
mod time;

pub mod app;
pub mod cli;
pub mod config;
pub mod error;
pub mod http;
pub mod identity;
pub mod managed_gateway;
pub mod observability;
pub mod runtime;
pub mod shutdown;
pub mod state;
pub mod zenoh;

pub use app::{ServerApplication, ServerBuilder};
pub use config::{
    AppConfig, ConfigOrigin, CorsConfig, HttpConfig, InariApiConfig, LoadedConfig, LogFormat,
    ManagedGatewayCertificateConfig, ManagedGatewayCertificateMode, ManagedGatewayConfig,
    ManagedGatewayDataPlaneConfig, ObservabilityConfig, RuntimeConfig, ServerConfig,
    StepCaSigningAlgorithm, ZenohAccessControlConfig, ZenohAclPermission, ZenohAdminSpaceConfig,
    ZenohConfig, ZenohMode, ZenohRestConfig, ZenohTlsConfig,
};
pub use coordination::{
    ChannelCapacity, ConcurrencyLimit, InariApiPermit, InariApiRequest, InvalidChannelCapacity,
    InvalidConcurrencyLimit, ZenohRestQueryPermit, ZenohRestRequest,
};
pub use error::{AppError, AppResult, ConfigError};
pub use observability::init as init_observability;
pub use runtime::build_runtime;
pub use state::{AppState, ComponentReadiness, ReadinessLevel, ReadinessSnapshot};
pub use zenoh::{
    KeyExpression, ZenohConnectionState, ZenohEvent, ZenohHandle, ZenohStatus, ZenohSupervisor,
};
