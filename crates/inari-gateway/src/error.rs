use thiserror::Error;

pub type GatewayResult<T> = Result<T, GatewayError>;

#[derive(Debug, Error)]
pub enum GatewayError {
    #[error("{0}")]
    InvalidInput(String),
    #[error("{0}")]
    Forbidden(String),
    #[error("{0}")]
    NotFound(String),
    #[error("{0}")]
    Conflict(String),
    #[error("{0}")]
    Unavailable(String),
    #[error("managed gateway persistence failed")]
    Persistence(#[from] sqlx::Error),
    #[error("managed gateway migration failed")]
    Migration(#[from] sqlx::migrate::MigrateError),
    #[error("managed gateway serialization failed")]
    Serialization(#[from] serde_json::Error),
    #[error("managed gateway state is inconsistent: {0}")]
    CorruptState(String),
    #[error("managed gateway filesystem operation failed")]
    Io(#[from] std::io::Error),
}
