use std::borrow::Cow;
use std::error::Error as StdError;
use std::io;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Duration;

use axum::Json;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde::Serialize;
use thiserror::Error;
use tower::BoxError;

pub type AppResult<T> = Result<T, AppError>;
type AnyError = Box<dyn StdError + Send + Sync + 'static>;

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("configuration file specified by {key} does not exist: {path}")]
    MissingExplicitPath { key: &'static str, path: PathBuf },
    #[error("{message}")]
    Invalid {
        message: Cow<'static, str>,
        #[source]
        source: Option<AnyError>,
    },
    #[error("failed to serialize the default configuration layer")]
    SerializeDefaults {
        #[source]
        source: toml::ser::Error,
    },
    #[error("failed to build layered configuration")]
    Build {
        #[source]
        source: config::ConfigError,
    },
    #[error("failed to deserialize layered configuration")]
    Deserialize {
        #[source]
        source: config::ConfigError,
    },
}

impl ConfigError {
    #[must_use]
    pub fn invalid(message: impl Into<Cow<'static, str>>) -> Self {
        Self::Invalid { message: message.into(), source: None }
    }

    #[must_use]
    pub fn with_source<E>(mut self, source: E) -> Self
    where
        E: StdError + Send + Sync + 'static,
    {
        if let Self::Invalid { source: inner_source, .. } = &mut self {
            *inner_source = Some(Box::new(source));
        }

        self
    }
}

#[derive(Debug, Error)]
pub enum AppError {
    #[error(transparent)]
    Config(#[from] ConfigError),
    #[error("failed to install observability subscriber")]
    Observability {
        #[source]
        source: AnyError,
    },
    #[error("failed to build Tokio runtime")]
    RuntimeBuild {
        #[source]
        source: io::Error,
    },
    #[error("failed to bind HTTP listener on {address}")]
    Bind {
        address: SocketAddr,
        #[source]
        source: io::Error,
    },
    #[error("HTTP server terminated unexpectedly")]
    Serve {
        #[source]
        source: io::Error,
    },
    #[error("failed to install shutdown signal handler")]
    Signal {
        #[source]
        source: io::Error,
    },
    #[error("background task failed")]
    TaskJoin {
        #[source]
        source: tokio::task::JoinError,
    },
    #[error("graceful shutdown exceeded {grace_period:?}")]
    GracefulShutdownTimeout { grace_period: Duration },
    #[error("request timed out")]
    RequestTimeout,
    #[error("{message}")]
    BadRequest { code: &'static str, message: Cow<'static, str> },
    #[error("{message}")]
    Forbidden { code: &'static str, message: Cow<'static, str> },
    #[error("{message}")]
    NotFound { code: &'static str, message: Cow<'static, str> },
    #[error("{message}")]
    NotImplemented { code: &'static str, message: Cow<'static, str> },
    #[error("{message}")]
    ServiceUnavailable { code: &'static str, message: Cow<'static, str> },
    #[error("{message}")]
    Internal {
        code: &'static str,
        message: Cow<'static, str>,
        #[source]
        source: Option<AnyError>,
    },
}

impl AppError {
    #[must_use]
    pub fn bad_request(message: impl Into<Cow<'static, str>>) -> Self {
        Self::BadRequest { code: "bad_request", message: message.into() }
    }

    #[must_use]
    pub fn forbidden(message: impl Into<Cow<'static, str>>) -> Self {
        Self::Forbidden { code: "forbidden", message: message.into() }
    }

    #[must_use]
    pub fn not_found(message: impl Into<Cow<'static, str>>) -> Self {
        Self::NotFound { code: "not_found", message: message.into() }
    }

    #[must_use]
    pub fn not_implemented(message: impl Into<Cow<'static, str>>) -> Self {
        Self::NotImplemented { code: "not_implemented", message: message.into() }
    }

    #[must_use]
    pub fn service_unavailable(message: impl Into<Cow<'static, str>>) -> Self {
        Self::ServiceUnavailable { code: "service_unavailable", message: message.into() }
    }

    #[must_use]
    pub fn internal(code: &'static str, message: impl Into<Cow<'static, str>>) -> Self {
        Self::Internal { code, message: message.into(), source: None }
    }

    #[must_use]
    pub fn with_source<E>(mut self, source: E) -> Self
    where
        E: StdError + Send + Sync + 'static,
    {
        self = self.with_boxed_source(Box::new(source));
        self
    }

    #[must_use]
    pub fn with_boxed_source(mut self, source: AnyError) -> Self {
        if let Self::Internal { source: inner_source, .. } = &mut self {
            *inner_source = Some(source);
        }

        self
    }

    #[must_use]
    pub fn code(&self) -> &'static str {
        match self {
            Self::Config(_) => "configuration_error",
            Self::Observability { .. } => "observability_error",
            Self::RuntimeBuild { .. } => "runtime_build_error",
            Self::Bind { .. } => "bind_error",
            Self::Serve { .. } => "serve_error",
            Self::Signal { .. } => "signal_error",
            Self::TaskJoin { .. } => "task_join_error",
            Self::GracefulShutdownTimeout { .. } => "shutdown_timeout",
            Self::RequestTimeout => "request_timeout",
            Self::BadRequest { code, .. }
            | Self::Forbidden { code, .. }
            | Self::NotFound { code, .. }
            | Self::NotImplemented { code, .. }
            | Self::ServiceUnavailable { code, .. }
            | Self::Internal { code, .. } => code,
        }
    }

    #[must_use]
    pub fn status_code(&self) -> StatusCode {
        match self {
            Self::BadRequest { .. } | Self::Config(_) => StatusCode::BAD_REQUEST,
            Self::Forbidden { .. } => StatusCode::FORBIDDEN,
            Self::NotFound { .. } => StatusCode::NOT_FOUND,
            Self::NotImplemented { .. } => StatusCode::NOT_IMPLEMENTED,
            Self::ServiceUnavailable { .. } | Self::GracefulShutdownTimeout { .. } => {
                StatusCode::SERVICE_UNAVAILABLE
            },
            Self::RequestTimeout => StatusCode::REQUEST_TIMEOUT,
            Self::Observability { .. }
            | Self::RuntimeBuild { .. }
            | Self::Bind { .. }
            | Self::Serve { .. }
            | Self::Signal { .. }
            | Self::TaskJoin { .. }
            | Self::Internal { .. } => StatusCode::INTERNAL_SERVER_ERROR,
        }
    }

    #[must_use]
    pub fn from_box_error(error: BoxError) -> Self {
        if error.is::<tower::timeout::error::Elapsed>() {
            return Self::RequestTimeout;
        }

        Self::internal("middleware_error", "The request failed inside the HTTP middleware stack.")
            .with_boxed_source(error)
    }

    pub fn log_for_server(self) -> Self {
        match &self {
            Self::BadRequest { .. }
            | Self::Forbidden { .. }
            | Self::NotFound { .. }
            | Self::NotImplemented { .. }
            | Self::ServiceUnavailable { .. }
            | Self::RequestTimeout => tracing::debug!(error = %self, code = self.code()),
            _ => tracing::error!(error = %self, code = self.code()),
        }

        self
    }
}

impl From<io::Error> for AppError {
    fn from(source: io::Error) -> Self {
        Self::Internal {
            code: "io_error",
            message: Cow::Borrowed("A local I/O operation failed."),
            source: Some(Box::new(source)),
        }
    }
}

impl From<BoxError> for AppError {
    fn from(error: BoxError) -> Self {
        Self::from_box_error(error)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ErrorEnvelope<'a> {
    error: ErrorBody<'a>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ErrorBody<'a> {
    code: &'a str,
    message: String,
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let status = self.status_code();
        let envelope =
            ErrorEnvelope { error: ErrorBody { code: self.code(), message: self.to_string() } };

        (status, Json(envelope)).into_response()
    }
}
