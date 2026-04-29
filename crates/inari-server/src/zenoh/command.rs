use bytes::Bytes;
use tokio::sync::oneshot;
use zenoh::Session;
use zenoh::bytes::Encoding;

use super::{KeyExpression, ZenohEvent};
use crate::error::{AppError, AppResult};
use crate::zenoh::session::{delete, publish};

#[derive(Debug)]
pub(super) enum Command {
    Publish {
        key: KeyExpression,
        payload: Bytes,
        encoding: Encoding,
        attachment: Option<Bytes>,
        respond_to: oneshot::Sender<AppResult<()>>,
    },
    Delete {
        key: KeyExpression,
        respond_to: oneshot::Sender<AppResult<()>>,
    },
}

#[derive(Debug)]
pub(super) struct CommandOutcome {
    pub(super) session_closed: bool,
    pub(super) error: Option<String>,
}

impl Command {
    pub(super) fn requested_event(&self) -> ZenohEvent {
        match self {
            Self::Publish { key, payload, .. } => {
                ZenohEvent::PublishRequested { bytes: payload.len(), key: key.clone() }
            },
            Self::Delete { key, .. } => ZenohEvent::DeleteRequested { key: key.clone() },
        }
    }

    pub(super) fn reject_unavailable(self, message: &'static str) {
        match self {
            Self::Publish { respond_to, .. } => {
                let _ = respond_to.send(Err(AppError::service_unavailable(message)));
            },
            Self::Delete { respond_to, .. } => {
                let _ = respond_to.send(Err(AppError::service_unavailable(message)));
            },
        }
    }

    pub(super) async fn execute_ready(self, session: &Session) -> CommandOutcome {
        match self {
            Self::Publish { key, payload, encoding, attachment, respond_to } => {
                let response = publish(session, &key, payload, encoding, attachment).await;

                let error = response
                    .as_ref()
                    .err()
                    .map(ToString::to_string);
                let session_closed = session.is_closed();

                let _ = respond_to.send(response);

                CommandOutcome { session_closed, error }
            },
            Self::Delete { key, respond_to } => {
                let response = delete(session, &key).await;

                let error = response
                    .as_ref()
                    .err()
                    .map(ToString::to_string);
                let session_closed = session.is_closed();

                let _ = respond_to.send(response);

                CommandOutcome { session_closed, error }
            },
        }
    }
}
