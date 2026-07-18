use thiserror::Error;

pub type AgentClientResult<T> = Result<T, AgentClientError>;

#[derive(Debug, Error)]
pub enum AgentClientError {
    #[error("the local agent is unavailable")]
    Unavailable(#[source] reqwest::Error),

    #[error("the local agent rejected the request")]
    Rejected,

    #[error("Device Center could not establish protected local trust")]
    PairingUnavailable(#[source] Box<dyn std::error::Error + Send + Sync>),

    #[error("the local event stream is unavailable")]
    EventStreamUnavailable(#[source] async_tungstenite::tungstenite::Error),

    #[error("the local agent returned data that this Device Center cannot understand")]
    InvalidResponse(#[source] Box<dyn std::error::Error + Send + Sync>),

    #[error("the local client identity is unavailable")]
    IdentityUnavailable(#[source] keyring::Error),

    #[error("the stored local client identity is malformed")]
    MalformedIdentity,

    #[error("Device Center must establish a protected local identity before it can continue")]
    IdentityRequired,

    #[error("the invitation is not a valid Inari invitation")]
    InvalidInvitation,

    #[error("the local event stream stopped unexpectedly")]
    EventStreamClosed,
}

impl AgentClientError {
    pub(crate) fn invalid_response(error: impl std::error::Error + Send + Sync + 'static) -> Self {
        Self::InvalidResponse(Box::new(error))
    }

    #[cfg(windows)]
    pub(crate) fn pairing_unavailable(
        error: impl std::error::Error + Send + Sync + 'static,
    ) -> Self {
        Self::PairingUnavailable(Box::new(error))
    }
}
