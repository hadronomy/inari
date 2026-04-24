use std::mem;
use std::time::{Duration, Instant};

use tokio::sync::{broadcast, mpsc, watch};
use tokio::{select, time};

use super::access::{SessionLease, SupervisorSignal};
use super::handle::{Command, ZenohHandle};
use super::session::{close_session, delete, open_session, publish};
use super::{ZenohEvent, ZenohStatus};
use crate::config::ZenohConfig;
use crate::error::{AppError, AppResult};
use crate::shutdown::ShutdownCoordinator;

#[derive(Debug)]
pub struct ZenohSupervisor {
    config: ZenohConfig,
    commands: mpsc::Receiver<Command>,
    signals: mpsc::Receiver<SupervisorSignal>,
    status: watch::Sender<ZenohStatus>,
    session: watch::Sender<Option<SessionLease>>,
    events: broadcast::Sender<ZenohEvent>,
    state_changed_at: Instant,
}

impl ZenohSupervisor {
    pub fn new(config: ZenohConfig) -> (ZenohHandle, Self) {
        let initial_status =
            if config.enabled { ZenohStatus::starting(0) } else { ZenohStatus::disabled() };
        let (commands_tx, commands) = mpsc::channel(config.command_buffer);
        let (signals_tx, signals) = mpsc::channel(32);
        let (status, status_rx) = watch::channel(initial_status);
        let (session, session_rx) = watch::channel(None);
        let (events, _) = broadcast::channel(config.event_buffer);

        (
            ZenohHandle::new(commands_tx, signals_tx, status_rx, session_rx, events.clone()),
            Self {
                config,
                commands,
                signals,
                status,
                session,
                events,
                state_changed_at: Instant::now(),
            },
        )
    }

    pub async fn run(mut self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        if !self.config.enabled {
            self.emit_event(ZenohEvent::Disabled);
            self.log_disabled();
            shutdown.wait_for_shutdown().await;
            self.set_session(None);
            self.set_status(ZenohStatus::shutting_down());
            self.emit_event(ZenohEvent::ShuttingDown);
            self.log_shutting_down();
            return Ok(());
        }

        let retry_interval = self
            .config
            .retry_interval
            .max(Duration::from_secs(1));
        let mut attempt = 0_u64;
        let mut generation = 0_u64;
        let mut state = SessionState::disconnected_now();

        loop {
            if let Some(session) = state.session().cloned() {
                select! {
                    biased;
                    _ = shutdown.wait_for_shutdown() => break,
                signal = self.signals.recv() => match self.handle_signal(signal, attempt).await? {
                    Control::Continue => {}
                    Control::Reconnect => {
                        if let Some(session) = state.take_session() {
                            close_session(session).await;
                            }
                            self.set_session(None);
                            state = SessionState::disconnected_after(retry_interval);
                        }
                        Control::Shutdown => break,
                    },
                    command = self.commands.recv() => match self.handle_command(command, Some(&session), attempt).await? {
                        Control::Continue => {}
                        Control::Reconnect => {
                            if let Some(session) = state.take_session() {
                                close_session(session).await;
                            }
                            self.set_session(None);
                            state = SessionState::disconnected_after(retry_interval);
                        }
                        Control::Shutdown => break,
                    }
                }
                continue;
            }

            let sleep = time::sleep_until(
                state
                    .next_attempt_at()
                    .expect("disconnected states carry a retry deadline"),
            );
            tokio::pin!(sleep);

            select! {
                biased;
                _ = shutdown.wait_for_shutdown() => break,
                signal = self.signals.recv() => match self.handle_signal(signal, attempt).await? {
                    Control::Continue => {}
                    Control::Reconnect => {
                        tracing::trace!(component = "zenoh", "ignoring reconnect request without an active session");
                    }
                    Control::Shutdown => break,
                },
                command = self.commands.recv() => match self.handle_command(command, None, attempt).await? {
                    Control::Continue => {}
                    Control::Reconnect => {
                        tracing::trace!(component = "zenoh", "ignoring reconnect request without an active session");
                    }
                    Control::Shutdown => break,
                },
                _ = &mut sleep => {
                    attempt += 1;
                    self.mark_connecting(attempt);

                    match open_session(&self.config).await {
                        Ok(session) => {
                            generation += 1;
                            let zid = session.zid().to_string();
                            self.set_status(ZenohStatus::connected(attempt));
                            self.set_session(Some(SessionLease::new(
                                session.clone(),
                                zid.clone(),
                                generation,
                            )));
                            self.emit_event(ZenohEvent::Connected { attempt });
                            self.log_connected(attempt, &zid);
                            state = SessionState::connected(session);
                        }
                        Err(error) => {
                            let message = error.to_string();
                            self.set_session(None);
                            self.set_status(ZenohStatus::degraded(attempt, message.clone()));
                            self.emit_event(ZenohEvent::Failed { attempt, message: message.clone() });
                            self.log_open_failed(attempt, &message);
                            state = SessionState::disconnected_after(retry_interval);
                        }
                    }
                }
            }
        }

        self.set_session(None);
        self.set_status(ZenohStatus::shutting_down());
        self.emit_event(ZenohEvent::ShuttingDown);
        self.log_shutting_down();
        if let Some(session) = state.take_session() {
            close_session(session).await;
        }

        Ok(())
    }

    async fn handle_command(
        &mut self,
        command: Option<Command>,
        session: Option<&::zenoh::Session>,
        attempt: u64,
    ) -> AppResult<Control> {
        let Some(command) = command else {
            return Ok(Control::Shutdown);
        };

        match command {
            Command::Publish { key, payload, encoding, respond_to } => {
                self.emit_event(ZenohEvent::PublishRequested {
                    bytes: payload.len(),
                    key: key.clone(),
                });

                let response = match session {
                    Some(active_session) => publish(active_session, &key, payload, encoding).await,
                    None => Err(AppError::service_unavailable("Zenoh session is not connected.")),
                };

                let should_reconnect = session.is_some() && response.is_err();
                if let Some(error) = response
                    .as_ref()
                    .err()
                    .filter(|_| should_reconnect)
                {
                    let message = error.to_string();
                    self.set_session(None);
                    self.set_status(ZenohStatus::reconnecting(attempt, message.clone()));
                    self.emit_event(ZenohEvent::Reconnecting { attempt, message: message.clone() });
                    self.log_session_lost(attempt, &message);
                }

                let _ = respond_to.send(response);
                Ok(if should_reconnect { Control::Reconnect } else { Control::Continue })
            },
            Command::Delete { key, respond_to } => {
                self.emit_event(ZenohEvent::DeleteRequested { key: key.clone() });

                let response = match session {
                    Some(active_session) => delete(active_session, &key).await,
                    None => Err(AppError::service_unavailable("Zenoh session is not connected.")),
                };

                let should_reconnect = session.is_some() && response.is_err();
                if let Some(error) = response
                    .as_ref()
                    .err()
                    .filter(|_| should_reconnect)
                {
                    let message = error.to_string();
                    self.set_session(None);
                    self.set_status(ZenohStatus::reconnecting(attempt, message.clone()));
                    self.emit_event(ZenohEvent::Reconnecting { attempt, message: message.clone() });
                    self.log_session_lost(attempt, &message);
                }

                let _ = respond_to.send(response);
                Ok(if should_reconnect { Control::Reconnect } else { Control::Continue })
            },
        }
    }

    async fn handle_signal(
        &mut self,
        signal: Option<SupervisorSignal>,
        attempt: u64,
    ) -> AppResult<Control> {
        let Some(signal) = signal else {
            return Ok(Control::Continue);
        };

        match signal {
            SupervisorSignal::SessionFault { message } => {
                self.set_session(None);
                self.set_status(ZenohStatus::reconnecting(attempt, message.clone()));
                self.emit_event(ZenohEvent::Reconnecting { attempt, message: message.clone() });
                self.log_session_lost(attempt, &message);
                Ok(Control::Reconnect)
            },
        }
    }

    fn mark_connecting(&mut self, attempt: u64) {
        if attempt == 1 {
            self.set_status(ZenohStatus::starting(attempt));
            self.emit_event(ZenohEvent::Connecting { attempt });
            self.log_connecting(attempt);
            return;
        }

        let message = "Retrying Zenoh session establishment.".to_owned();
        self.set_status(ZenohStatus::reconnecting(attempt, message.clone()));
        self.emit_event(ZenohEvent::Reconnecting { attempt, message });
        self.log_reconnecting(attempt);
    }

    fn set_status(&self, next: ZenohStatus) {
        self.status.send_replace(next);
    }

    fn set_session(&self, next: Option<SessionLease>) {
        self.session.send_replace(next);
    }

    fn emit_event(&self, event: ZenohEvent) {
        if let Err(error) = self.events.send(event) {
            tracing::trace!(event = %error.0, "dropping Zenoh event without subscribers");
        }
    }

    fn log_disabled(&mut self) {
        let state_elapsed_ms = self.take_state_elapsed_ms();
        tracing::info!(
            component = "zenoh",
            state = "disabled",
            state_elapsed_ms,
            "zenoh integration disabled"
        );
    }

    fn log_connecting(&mut self, attempt: u64) {
        let state_elapsed_ms = self.take_state_elapsed_ms();
        tracing::info!(
            component = "zenoh",
            state = "connecting",
            attempt,
            state_elapsed_ms,
            "zenoh connection attempt started"
        );
    }

    fn log_reconnecting(&mut self, attempt: u64) {
        let state_elapsed_ms = self.take_state_elapsed_ms();
        tracing::info!(
            component = "zenoh",
            state = "reconnecting",
            attempt,
            state_elapsed_ms,
            "zenoh reconnection attempt started"
        );
    }

    fn log_connected(&mut self, attempt: u64, zid: &str) {
        let state_elapsed_ms = self.take_state_elapsed_ms();
        tracing::info!(
            component = "zenoh",
            state = "connected",
            attempt,
            zid,
            state_elapsed_ms,
            "zenoh session established"
        );
    }

    fn log_open_failed(&mut self, attempt: u64, error: &str) {
        let state_elapsed_ms = self.take_state_elapsed_ms();
        tracing::warn!(
            component = "zenoh",
            state = "degraded",
            attempt,
            error,
            state_elapsed_ms,
            "zenoh session open failed"
        );
    }

    fn log_session_lost(&mut self, attempt: u64, error: &str) {
        let state_elapsed_ms = self.take_state_elapsed_ms();
        tracing::warn!(
            component = "zenoh",
            state = "reconnecting",
            attempt,
            error,
            state_elapsed_ms,
            "zenoh session lost"
        );
    }

    fn log_shutting_down(&mut self) {
        let state_elapsed_ms = self.take_state_elapsed_ms();
        tracing::info!(
            component = "zenoh",
            state = "shutting_down",
            state_elapsed_ms,
            "zenoh supervisor stopping"
        );
    }

    fn take_state_elapsed_ms(&mut self) -> u64 {
        let elapsed = self.state_changed_at.elapsed();
        self.state_changed_at = Instant::now();
        elapsed
            .as_millis()
            .try_into()
            .unwrap_or(u64::MAX)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Control {
    Continue,
    Reconnect,
    Shutdown,
}

#[derive(Debug)]
enum SessionState {
    Disconnected { next_attempt_at: time::Instant },
    Connected { session: ::zenoh::Session },
}

impl SessionState {
    fn disconnected_now() -> Self {
        Self::Disconnected { next_attempt_at: time::Instant::now() }
    }

    fn disconnected_after(delay: Duration) -> Self {
        Self::Disconnected { next_attempt_at: time::Instant::now() + delay }
    }

    fn connected(session: ::zenoh::Session) -> Self {
        Self::Connected { session }
    }

    fn session(&self) -> Option<&::zenoh::Session> {
        match self {
            Self::Disconnected { .. } => None,
            Self::Connected { session } => Some(session),
        }
    }

    fn next_attempt_at(&self) -> Option<time::Instant> {
        match self {
            Self::Disconnected { next_attempt_at } => Some(*next_attempt_at),
            Self::Connected { .. } => None,
        }
    }

    fn take_session(&mut self) -> Option<::zenoh::Session> {
        match mem::replace(self, Self::disconnected_now()) {
            Self::Disconnected { .. } => None,
            Self::Connected { session } => Some(session),
        }
    }
}
