use thiserror::Error;

pub type RouterResult<T> = Result<T, RouterError>;

#[derive(Debug, Error)]
pub enum RouterError {
    #[error("Invalid Router policy: {0}")]
    InvalidPolicy(String),
    #[error("Router policy signature is invalid.")]
    InvalidSignature,
    #[error("Router policy is not current.")]
    NotCurrent,
    #[error("Router policy generation is stale or conflicts with stored policy.")]
    GenerationConflict,
    #[error("Router is not ready: {0}")]
    NotReady(String),
    #[error("Router Supervisor is busy or unavailable.")]
    Unavailable,
    #[error("Router storage or process error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Router JSON error: {0}")]
    Json(#[from] serde_json::Error),
}
