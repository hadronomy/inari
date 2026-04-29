use std::time::Instant;

use tokio::sync::{broadcast, watch};

use super::attempt::Attempt;
use super::state::{Degraded, Opening, Ready, State};
use crate::zenoh::{CurrentSession, ZenohEvent, ZenohStatus};

#[derive(Debug)]
pub(super) struct RuntimePublisher {
    status: watch::Sender<ZenohStatus>,
    session: watch::Sender<Option<CurrentSession>>,
    events: broadcast::Sender<ZenohEvent>,
    clock: StateClock,
}

impl RuntimePublisher {
    pub(super) fn new(
        status: watch::Sender<ZenohStatus>,
        session: watch::Sender<Option<CurrentSession>>,
        events: broadcast::Sender<ZenohEvent>,
    ) -> Self {
        Self { status, session, events, clock: StateClock::new() }
    }

    pub(super) fn enter_disabled(&mut self) {
        self.enter(&Disabled);
    }

    pub(super) fn enter_state(&mut self, state: &State) {
        self.enter(state);
    }

    pub(super) fn enter_shutting_down(&mut self) {
        self.enter(&ShuttingDown);
    }

    pub(super) fn record(&self, event: ZenohEvent) {
        if let Err(error) = self.events.send(event) {
            tracing::trace!(
                event = %error.0,
                "dropping Zenoh event without subscribers"
            );
        }
    }

    fn enter<S>(&mut self, state: &S)
    where
        S: PublishState,
    {
        let publication = state.publication();

        self.status
            .send_replace(publication.status);
        self.session
            .send_replace(publication.session.into_option());

        if let Some(event) = publication.event {
            self.record(event);
        }

        publication
            .log
            .emit(self.clock.take_elapsed_ms());
    }
}

#[derive(Debug)]
struct StateClock {
    changed_at: Instant,
}

impl StateClock {
    fn new() -> Self {
        Self { changed_at: Instant::now() }
    }

    fn take_elapsed_ms(&mut self) -> u64 {
        let elapsed = self.changed_at.elapsed();
        self.changed_at = Instant::now();

        elapsed
            .as_millis()
            .try_into()
            .unwrap_or(u64::MAX)
    }
}

#[derive(Debug)]
struct Publication {
    status: ZenohStatus,
    session: SessionSnapshot,
    event: Option<ZenohEvent>,
    log: LogRecord,
}

#[derive(Debug)]
enum SessionSnapshot {
    Disconnected,
    Connected(CurrentSession),
}

impl SessionSnapshot {
    fn into_option(self) -> Option<CurrentSession> {
        match self {
            Self::Disconnected => None,
            Self::Connected(session) => Some(session),
        }
    }
}

#[derive(Debug)]
enum LogRecord {
    Disabled,
    Connecting { attempt: Attempt },
    Reconnecting { attempt: Attempt },
    Connected { attempt: Attempt, zid: String },
    Degraded { attempt: Attempt, error: String },
    ShuttingDown,
}

impl LogRecord {
    fn emit(self, state_elapsed_ms: u64) {
        match self {
            Self::Disabled => {
                tracing::info!(
                    component = "zenoh",
                    state = "disabled",
                    state_elapsed_ms,
                    "zenoh integration disabled"
                );
            },

            Self::Connecting { attempt } => {
                tracing::info!(
                    component = "zenoh",
                    state = "connecting",
                    attempt = u64::from(attempt),
                    state_elapsed_ms,
                    "zenoh session opening"
                );
            },

            Self::Reconnecting { attempt } => {
                tracing::info!(
                    component = "zenoh",
                    state = "reconnecting",
                    attempt = u64::from(attempt),
                    state_elapsed_ms,
                    "zenoh session reopening after failed open or closed session"
                );
            },

            Self::Connected { attempt, zid } => {
                tracing::info!(
                    component = "zenoh",
                    state = "connected",
                    attempt = u64::from(attempt),
                    zid,
                    state_elapsed_ms,
                    "zenoh session available"
                );
            },

            Self::Degraded { attempt, error } => {
                tracing::warn!(
                    component = "zenoh",
                    state = "degraded",
                    attempt = u64::from(attempt),
                    error,
                    state_elapsed_ms,
                    "zenoh session unavailable"
                );
            },

            Self::ShuttingDown => {
                tracing::info!(
                    component = "zenoh",
                    state = "shutting_down",
                    state_elapsed_ms,
                    "zenoh supervisor stopping"
                );
            },
        }
    }
}

trait PublishState {
    fn publication(&self) -> Publication;
}

#[derive(Debug)]
struct Disabled;

impl PublishState for Disabled {
    fn publication(&self) -> Publication {
        Publication {
            status: ZenohStatus::disabled(),
            session: SessionSnapshot::Disconnected,
            event: Some(ZenohEvent::Disabled),
            log: LogRecord::Disabled,
        }
    }
}

#[derive(Debug)]
struct ShuttingDown;

impl PublishState for ShuttingDown {
    fn publication(&self) -> Publication {
        Publication {
            status: ZenohStatus::shutting_down(),
            session: SessionSnapshot::Disconnected,
            event: Some(ZenohEvent::ShuttingDown),
            log: LogRecord::ShuttingDown,
        }
    }
}

impl PublishState for State {
    fn publication(&self) -> Publication {
        match self {
            Self::Opening(opening) => opening.publication(),
            Self::Ready(ready) => ready.publication(),
            Self::Degraded(degraded) => degraded.publication(),
            Self::ShuttingDown(_) => ShuttingDown.publication(),
        }
    }
}

impl Opening {
    fn publication(&self) -> Publication {
        if self.attempt.is_first() {
            return Publication {
                status: ZenohStatus::starting(self.attempt.into()),
                session: SessionSnapshot::Disconnected,
                event: Some(ZenohEvent::Connecting { attempt: self.attempt.into() }),
                log: LogRecord::Connecting { attempt: self.attempt },
            };
        }

        let message = "Retrying Zenoh session open.".to_owned();

        Publication {
            status: ZenohStatus::reconnecting(self.attempt.into(), message.clone()),
            session: SessionSnapshot::Disconnected,
            event: Some(ZenohEvent::Reconnecting { attempt: self.attempt.into(), message }),
            log: LogRecord::Reconnecting { attempt: self.attempt },
        }
    }
}

impl Ready {
    fn publication(&self) -> Publication {
        Publication {
            status: ZenohStatus::connected(self.attempt.into()),
            session: SessionSnapshot::Connected(self.session.clone()),
            event: Some(ZenohEvent::Connected { attempt: self.attempt.into() }),
            log: LogRecord::Connected {
                attempt: self.attempt,
                zid: self.session.zid().to_string(),
            },
        }
    }
}

impl Degraded {
    fn publication(&self) -> Publication {
        Publication {
            status: ZenohStatus::degraded(self.attempt.into(), self.error.clone()),
            session: SessionSnapshot::Disconnected,
            event: Some(ZenohEvent::Failed {
                attempt: self.attempt.into(),
                message: self.error.clone(),
            }),
            log: LogRecord::Degraded { attempt: self.attempt, error: self.error.clone() },
        }
    }
}
