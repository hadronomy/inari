use std::num::NonZeroU64;
use std::time::{Duration, Instant};

use tokio::sync::{broadcast, mpsc, watch};
use tokio::{select, time};
use zenoh::Session;

use super::access::CurrentSession;
use super::handle::{Command, ZenohHandle};
use super::session::{close_session, delete, open_session, publish};
use super::{ZenohEvent, ZenohStatus};
use crate::config::ZenohConfig;
use crate::error::{AppError, AppResult};
use crate::shutdown::ShutdownCoordinator;
use crate::zenoh::access::Generation;

#[derive(Debug)]
pub(crate) enum SupervisorSignal {
    OperationFailed { message: String },
}

#[derive(Debug)]
pub struct ZenohSupervisor {
    mode: SupervisorMode,
    io: RuntimeIo,
    publisher: RuntimePublisher,
}

impl ZenohSupervisor {
    pub fn new(config: ZenohConfig) -> (ZenohHandle, Self) {
        let enabled = config.enabled;
        let command_buffer = config.command_buffer;
        let event_buffer = config.event_buffer;

        let initial_status =
            if enabled { ZenohStatus::starting(0) } else { ZenohStatus::disabled() };

        let (commands_tx, commands) = mpsc::channel(command_buffer);
        let (signals_tx, signals) = mpsc::channel(32);
        let (status, status_rx) = watch::channel(initial_status);
        let (session, session_rx) = watch::channel(None);
        let (events, _) = broadcast::channel(event_buffer);

        let handle =
            ZenohHandle::new(commands_tx, signals_tx, status_rx, session_rx, events.clone());

        let supervisor = Self {
            mode: SupervisorMode::from(config),
            io: RuntimeIo { commands, signals },
            publisher: RuntimePublisher { status, session, events, clock: StateClock::new() },
        };

        (handle, supervisor)
    }

    pub async fn run(self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        let Self { mode, io, publisher } = self;

        match mode {
            SupervisorMode::Disabled => {
                DisabledSupervisor { io, publisher }
                    .run(shutdown)
                    .await
            },

            SupervisorMode::Enabled(config) => {
                EnabledSupervisor::initial(config, io, publisher)
                    .run(shutdown)
                    .await
            },
        }
    }
}

#[derive(Debug)]
enum SupervisorMode {
    Disabled,
    Enabled(EnabledZenohConfig),
}

impl From<ZenohConfig> for SupervisorMode {
    fn from(config: ZenohConfig) -> Self {
        if config.enabled { Self::Enabled(EnabledZenohConfig::new(config)) } else { Self::Disabled }
    }
}

#[derive(Debug)]
struct EnabledZenohConfig {
    raw: ZenohConfig,
    retry_interval: Duration,
}

impl EnabledZenohConfig {
    const MIN_RETRY_INTERVAL: Duration = Duration::from_secs(1);

    fn new(config: ZenohConfig) -> Self {
        debug_assert!(config.enabled);

        Self {
            retry_interval: config
                .open_retry_interval
                .max(Self::MIN_RETRY_INTERVAL),
            raw: config,
        }
    }

    fn raw(&self) -> &ZenohConfig {
        &self.raw
    }

    fn retry_interval(&self) -> Duration {
        self.retry_interval
    }
}

// === Disabled supervisor =====================================================

#[derive(Debug)]
struct DisabledSupervisor {
    io: RuntimeIo,
    publisher: RuntimePublisher,
}

impl DisabledSupervisor {
    async fn run(mut self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        self.publisher.enter(&Disabled);

        let mut signals_closed = false;

        loop {
            let event = select! {
                biased;

                _ = shutdown.wait_for_shutdown() => DisabledEvent::Shutdown,

                signal = self.io.signals.recv(), if !signals_closed => {
                    DisabledEvent::Signal(signal)
                }

                command = self.io.commands.recv() => {
                    DisabledEvent::Command(command)
                }
            };

            match event {
                DisabledEvent::Shutdown => break,

                DisabledEvent::Signal(Some(_)) => {
                    tracing::trace!(
                        component = "zenoh",
                        state = "disabled",
                        "ignoring Zenoh supervisor signal while disabled"
                    );
                },

                DisabledEvent::Signal(None) => {
                    signals_closed = true;
                },

                DisabledEvent::Command(Some(command)) => {
                    self.publisher
                        .record(command.requested_event());
                    command.reject_unavailable("Zenoh integration is disabled.");
                },

                DisabledEvent::Command(None) => break,
            }
        }

        self.publisher.enter(&ShuttingDown);

        Ok(())
    }
}

#[derive(Debug)]
enum DisabledEvent {
    Shutdown,
    Signal(Option<SupervisorSignal>),
    Command(Option<Command>),
}

// === Enabled supervisor ======================================================

#[derive(Debug)]
struct EnabledSupervisor {
    config: EnabledZenohConfig,
    io: RuntimeIo,
    publisher: RuntimePublisher,
    attempts: AttemptCounter,
    generation: Generation,
    signals_closed: bool,
}

impl EnabledSupervisor {
    fn initial(config: EnabledZenohConfig, io: RuntimeIo, publisher: RuntimePublisher) -> Self {
        Self {
            config,
            io,
            publisher,
            attempts: AttemptCounter::new(),
            generation: Generation::ZERO,
            signals_closed: false,
        }
    }

    async fn run(mut self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        let first_attempt = self.next_attempt();
        let mut state = self.enter(State::Opening(Opening { attempt: first_attempt }));

        loop {
            state = match state {
                State::Opening(opening) => {
                    self.step_opening(opening, &shutdown)
                        .await
                },

                State::Ready(ready) => self.step_ready(ready, &shutdown).await,

                State::Degraded(degraded) => {
                    self.step_degraded(degraded, &shutdown)
                        .await
                },

                State::ShuttingDown(shutting_down) => {
                    self.finish_shutdown(shutting_down)
                        .await;
                    return Ok(());
                },
            };
        }
    }

    async fn step_opening(&mut self, opening: Opening, shutdown: &ShutdownCoordinator) -> State {
        let event = select! {
            biased;

            _ = shutdown.wait_for_shutdown() => OpeningEvent::Shutdown,

            result = open_session(self.config.raw()) => {
                OpeningEvent::Opened(result)
            }
        };

        match event {
            OpeningEvent::Shutdown => {
                self.enter(State::ShuttingDown(ShuttingDownState { session: None }))
            },

            OpeningEvent::Opened(Ok(session)) => {
                let generation = self.next_generation();
                let lease = CurrentSession::new(session, generation);

                self.enter(State::Ready(Ready { session: lease, attempt: opening.attempt }))
            },

            OpeningEvent::Opened(Err(error)) => self.enter(State::Degraded(Degraded::after(
                opening.attempt,
                error.to_string(),
                self.config.retry_interval(),
            ))),
        }
    }

    async fn step_ready(&mut self, ready: Ready, shutdown: &ShutdownCoordinator) -> State {
        let event = select! {
            biased;

            _ = shutdown.wait_for_shutdown() => ReadyEvent::Shutdown,

            signal = self.io.signals.recv(), if !self.signals_closed => {
                ReadyEvent::Signal(signal)
            }

            command = self.io.commands.recv() => {
                ReadyEvent::Command(command)
            }
        };

        match event {
            ReadyEvent::Shutdown => {
                self.enter(State::ShuttingDown(ShuttingDownState { session: Some(ready.session) }))
            },

            ReadyEvent::Signal(Some(signal)) => self.handle_ready_signal(ready, signal),

            ReadyEvent::Signal(None) => {
                self.signals_closed = true;
                State::Ready(ready)
            },

            ReadyEvent::Command(Some(command)) => {
                self.handle_ready_command(ready, command)
                    .await
            },

            ReadyEvent::Command(None) => {
                self.enter(State::ShuttingDown(ShuttingDownState { session: Some(ready.session) }))
            },
        }
    }

    async fn step_degraded(&mut self, degraded: Degraded, shutdown: &ShutdownCoordinator) -> State {
        let sleep = time::sleep_until(degraded.retry_at);
        tokio::pin!(sleep);

        let event = select! {
            biased;

            _ = shutdown.wait_for_shutdown() => DegradedEvent::Shutdown,

            signal = self.io.signals.recv(), if !self.signals_closed => {
                DegradedEvent::Signal(signal)
            }

            command = self.io.commands.recv() => {
                DegradedEvent::Command(command)
            }

            _ = &mut sleep => DegradedEvent::Retry,
        };

        match event {
            DegradedEvent::Shutdown => {
                self.enter(State::ShuttingDown(ShuttingDownState { session: None }))
            },

            DegradedEvent::Signal(Some(signal)) => {
                degraded.ignore_signal(signal);
                State::Degraded(degraded)
            },

            DegradedEvent::Signal(None) => {
                self.signals_closed = true;
                State::Degraded(degraded)
            },

            DegradedEvent::Command(Some(command)) => {
                self.publisher
                    .record(command.requested_event());
                command.reject_unavailable(degraded.unavailable_message());

                State::Degraded(degraded)
            },

            DegradedEvent::Command(None) => {
                self.enter(State::ShuttingDown(ShuttingDownState { session: None }))
            },

            DegradedEvent::Retry => {
                let attempt = self.next_attempt();

                self.enter(State::Opening(Opening { attempt }))
            },
        }
    }

    async fn finish_shutdown(&mut self, shutting_down: ShuttingDownState) {
        if let Some(session) = shutting_down.session {
            close_session(session.session().clone()).await;
        }
    }

    fn handle_ready_signal(&mut self, ready: Ready, signal: SupervisorSignal) -> State {
        match signal {
            SupervisorSignal::OperationFailed { message } => {
                if ready.session.session().is_closed() {
                    self.enter(State::Degraded(Degraded::after(
                        ready.attempt,
                        message,
                        self.config.retry_interval(),
                    )))
                } else {
                    tracing::trace!(
                        component = "zenoh",
                        state = "ready",
                        generation = u64::from(ready.session.generation()),
                        error = %message,
                        "ignoring Zenoh supervisor signal because the session remains open"
                    );

                    State::Ready(ready)
                }
            },
        }
    }

    async fn handle_ready_command(&mut self, ready: Ready, command: Command) -> State {
        self.publisher
            .record(command.requested_event());

        let outcome = command
            .execute_ready(ready.session.session())
            .await;

        if outcome.session_closed {
            let message = outcome
                .error
                .unwrap_or_else(|| "Zenoh session is closed.".to_owned());

            return self.enter(State::Degraded(Degraded::after(
                ready.attempt,
                message,
                self.config.retry_interval(),
            )));
        }

        State::Ready(ready)
    }

    fn enter(&mut self, state: State) -> State {
        self.publisher.enter(&state);
        state
    }

    fn next_attempt(&mut self) -> Attempt {
        self.attempts.next()
    }

    fn next_generation(&mut self) -> Generation {
        self.generation = self.generation.next();
        self.generation
    }
}

#[derive(Debug)]
enum OpeningEvent {
    Shutdown,
    Opened(AppResult<Session>),
}

#[derive(Debug)]
enum ReadyEvent {
    Shutdown,
    Signal(Option<SupervisorSignal>),
    Command(Option<Command>),
}

#[derive(Debug)]
enum DegradedEvent {
    Shutdown,
    Signal(Option<SupervisorSignal>),
    Command(Option<Command>),
    Retry,
}

#[derive(Debug)]
struct RuntimeIo {
    commands: mpsc::Receiver<Command>,
    signals: mpsc::Receiver<SupervisorSignal>,
}

// === Runtime states ==========================================================

#[derive(Debug)]
enum State {
    Opening(Opening),
    Ready(Ready),
    Degraded(Degraded),
    ShuttingDown(ShuttingDownState),
}

#[derive(Debug)]
struct Opening {
    attempt: Attempt,
}

#[derive(Debug)]
struct Ready {
    session: CurrentSession,
    attempt: Attempt,
}

#[derive(Debug)]
struct Degraded {
    attempt: Attempt,
    error: String,
    retry_at: time::Instant,
}

impl Degraded {
    fn after(attempt: Attempt, error: String, delay: Duration) -> Self {
        Self { attempt, error, retry_at: time::Instant::now() + delay }
    }

    fn unavailable_message(&self) -> &'static str {
        "Zenoh session is unavailable."
    }

    fn ignore_signal(&self, signal: SupervisorSignal) {
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
struct ShuttingDownState {
    session: Option<CurrentSession>,
}

// === Runtime publication =====================================================

#[derive(Debug)]
struct RuntimePublisher {
    status: watch::Sender<ZenohStatus>,
    session: watch::Sender<Option<CurrentSession>>,
    events: broadcast::Sender<ZenohEvent>,
    clock: StateClock,
}

impl RuntimePublisher {
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

    fn record(&self, event: ZenohEvent) {
        if let Err(error) = self.events.send(event) {
            tracing::trace!(
                event = %error.0,
                "dropping Zenoh event without subscribers"
            );
        }
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

// === Lifecycle publication ===================================================

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

// === Domain counters =========================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct Attempt(NonZeroU64);

impl Attempt {
    fn is_first(self) -> bool {
        self.0.get() == 1
    }
}

impl From<Attempt> for u64 {
    fn from(attempt: Attempt) -> Self {
        attempt.0.get()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct AttemptCounter {
    current: u64,
}

impl AttemptCounter {
    const fn new() -> Self {
        Self { current: 0 }
    }

    fn next(&mut self) -> Attempt {
        self.current = self.current.saturating_add(1).max(1);

        let value = NonZeroU64::new(self.current)
            .expect("attempt counter is always non-zero after increment");

        Attempt(value)
    }
}

// === Command behavior ========================================================

#[derive(Debug)]
struct CommandOutcome {
    session_closed: bool,
    error: Option<String>,
}

impl Command {
    fn requested_event(&self) -> ZenohEvent {
        match self {
            Self::Publish { key, payload, .. } => {
                ZenohEvent::PublishRequested { bytes: payload.len(), key: key.clone() }
            },

            Self::Delete { key, .. } => ZenohEvent::DeleteRequested { key: key.clone() },
        }
    }

    fn reject_unavailable(self, message: &'static str) {
        match self {
            Self::Publish { respond_to, .. } => {
                let _ = respond_to.send(Err(AppError::service_unavailable(message)));
            },

            Self::Delete { respond_to, .. } => {
                let _ = respond_to.send(Err(AppError::service_unavailable(message)));
            },
        }
    }

    async fn execute_ready(self, session: &Session) -> CommandOutcome {
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
