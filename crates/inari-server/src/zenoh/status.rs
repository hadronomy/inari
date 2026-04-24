use std::fmt;

use chrono::{DateTime, Utc};
use serde::Serialize;

use super::KeyExpression;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ZenohConnectionState {
    Disabled,
    Starting,
    Connected,
    Reconnecting,
    Degraded,
    ShuttingDown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ZenohStatus {
    pub state: ZenohConnectionState,
    pub attempt: u64,
    pub message: Option<String>,
    pub observed_at: DateTime<Utc>,
}

impl ZenohStatus {
    pub fn disabled() -> Self {
        Self::new(ZenohConnectionState::Disabled, 0, Some("Zenoh is disabled.".into()))
    }

    pub fn starting(attempt: u64) -> Self {
        Self::new(
            ZenohConnectionState::Starting,
            attempt,
            Some("Attempting to establish the Zenoh session.".into()),
        )
    }

    pub fn connected(attempt: u64) -> Self {
        Self::new(
            ZenohConnectionState::Connected,
            attempt,
            Some("Zenoh session established.".into()),
        )
    }

    pub fn reconnecting(attempt: u64, message: impl Into<String>) -> Self {
        Self::new(ZenohConnectionState::Reconnecting, attempt, Some(message.into()))
    }

    pub fn degraded(attempt: u64, message: impl Into<String>) -> Self {
        Self::new(ZenohConnectionState::Degraded, attempt, Some(message.into()))
    }

    pub fn shutting_down() -> Self {
        Self::new(
            ZenohConnectionState::ShuttingDown,
            0,
            Some("Zenoh supervisor is shutting down.".into()),
        )
    }

    fn new(state: ZenohConnectionState, attempt: u64, message: Option<String>) -> Self {
        Self { state, attempt, message, observed_at: Utc::now() }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ZenohEvent {
    Disabled,
    Connecting { attempt: u64 },
    Connected { attempt: u64 },
    Reconnecting { attempt: u64, message: String },
    Failed { attempt: u64, message: String },
    ShuttingDown,
    PublishRequested { key: KeyExpression, bytes: usize },
    DeleteRequested { key: KeyExpression },
}

impl fmt::Display for ZenohEvent {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Disabled => f.write_str("disabled"),
            Self::Connecting { attempt } => write!(f, "connecting(attempt={attempt})"),
            Self::Connected { attempt } => write!(f, "connected(attempt={attempt})"),
            Self::Reconnecting { attempt, message } => {
                write!(f, "reconnecting(attempt={attempt}, message={message})")
            }
            Self::Failed { attempt, message } => {
                write!(f, "failed(attempt={attempt}, message={message})")
            }
            Self::ShuttingDown => f.write_str("shutting_down"),
            Self::PublishRequested { key, bytes } => {
                write!(f, "publish_requested(key={key}, bytes={bytes})")
            }
            Self::DeleteRequested { key } => write!(f, "delete_requested(key={key})"),
        }
    }
}
