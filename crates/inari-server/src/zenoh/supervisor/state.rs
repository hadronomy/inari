use std::time::Duration;

use tokio::time;

use super::SupervisorSignal;
use super::attempt::Attempt;
use crate::zenoh::CurrentSession;

#[derive(Debug)]
pub(super) enum State {
    Opening(Opening),
    Ready(Ready),
    Degraded(Degraded),
    ShuttingDown(ShuttingDownState),
}

#[derive(Debug)]
pub(super) struct Opening {
    pub(super) attempt: Attempt,
}

#[derive(Debug)]
pub(super) struct Ready {
    pub(super) session: CurrentSession,
    pub(super) attempt: Attempt,
}

#[derive(Debug)]
pub(super) struct Degraded {
    pub(super) attempt: Attempt,
    pub(super) error: String,
    pub(super) retry_at: time::Instant,
}

impl Degraded {
    pub(super) fn after(attempt: Attempt, error: String, delay: Duration) -> Self {
        Self { attempt, error, retry_at: time::Instant::now() + delay }
    }

    pub(super) fn unavailable_message(&self) -> &'static str {
        "Zenoh session is unavailable."
    }

    pub(super) fn ignore_signal(&self, signal: SupervisorSignal) {
        match signal {
            SupervisorSignal::OperationFailed { message } => {
                tracing::trace!(
                    component = "zenoh",
                    state = "degraded",
                    attempt = u64::from(self.attempt),
                    current_error = %self.error,
                    error = %message,
                    "ignoring Zenoh supervisor signal while degraded"
                );
            },
        }
    }
}

#[derive(Debug)]
pub(super) struct ShuttingDownState {
    pub(super) session: Option<CurrentSession>,
}
