use tracing_subscriber::{EnvFilter, layer::SubscriberExt, util::SubscriberInitExt};

use crate::{
    config::{LogFormat, ObservabilityConfig},
    error::AppError,
};

pub fn init(config: &ObservabilityConfig) -> Result<(), AppError> {
    let env_filter = EnvFilter::try_from_default_env()
        .or_else(|_| EnvFilter::try_new(&config.filter))
        .map_err(|source| AppError::Observability { source: Box::new(source) })?;

    match config.format {
        LogFormat::Pretty => tracing_subscriber::registry()
            .with(env_filter)
            .with(
                tracing_subscriber::fmt::layer()
                    .with_target(config.include_targets)
                    .with_thread_ids(config.include_thread_ids)
                    .with_thread_names(config.include_thread_names)
                    .compact(),
            )
            .try_init()
            .map_err(|source| AppError::Observability { source: Box::new(source) }),
        LogFormat::Json => tracing_subscriber::registry()
            .with(env_filter)
            .with(
                tracing_subscriber::fmt::layer()
                    .json()
                    .with_target(config.include_targets)
                    .with_thread_ids(config.include_thread_ids)
                    .with_thread_names(config.include_thread_names)
                    .flatten_event(true),
            )
            .try_init()
            .map_err(|source| AppError::Observability { source: Box::new(source) }),
    }
}
