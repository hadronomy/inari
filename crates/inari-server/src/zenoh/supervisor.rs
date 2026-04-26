use std::time::{Duration, Instant};

use tokio::sync::{broadcast, mpsc, watch};
use tokio::{select, time};
use zenoh::Session;

use super::access::{SessionLease, SupervisorSignal};
use super::handle::{Command, ZenohHandle};
use super::session::{close_session, delete, open_session, publish};
use super::{ZenohEvent, ZenohStatus};
use crate::config::ZenohConfig;
use crate::error::{AppError, AppResult};
use crate::shutdown::ShutdownCoordinator;

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
                let machine = Supervisor::<Disconnected>::initial(config, io, publisher);
                Machine::Disconnected(machine)
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
        match EnabledZenohConfig::try_from(config) {
            Ok(config) => Self::Enabled(config),
            Err(_config) => Self::Disabled,
        }
    }
}

#[derive(Debug)]
struct EnabledZenohConfig {
    raw: ZenohConfig,
    retry_interval: Duration,
}

impl EnabledZenohConfig {
    const MIN_RETRY_INTERVAL: Duration = Duration::from_secs(1);

    fn raw(&self) -> &ZenohConfig {
        &self.raw
    }

    fn retry_interval(&self) -> Duration {
        self.retry_interval
    }
}

impl TryFrom<ZenohConfig> for EnabledZenohConfig {
    type Error = ZenohConfig;

    fn try_from(config: ZenohConfig) -> Result<Self, Self::Error> {
        if !config.enabled {
            return Err(config);
        }

        Ok(Self {
            retry_interval: config
                .retry_interval
                .max(Self::MIN_RETRY_INTERVAL),
            raw: config,
        })
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
                        .record(CommandRequested::from(&command));
                    command.reject_unavailable("Zenoh integration is disabled.");
                },

                DisabledEvent::Command(None) => break,
            }
        }

        self.publisher
            .enter(&ShuttingDown::without_active());

        Ok(())
    }
}

#[derive(Debug)]
enum DisabledEvent {
    Shutdown,
    Signal(Option<SupervisorSignal>),
    Command(Option<Command>),
}

// === Enabled typestate machine ==============================================

#[derive(Debug)]
enum Machine {
    Disconnected(Supervisor<Disconnected>),
    Connecting(Supervisor<Connecting>),
    Connected(Supervisor<Connected>),
    Reconnecting(Supervisor<Reconnecting>),
    Degraded(Supervisor<Degraded>),
    ShuttingDown(Supervisor<ShuttingDown>),
}

impl Machine {
    async fn run(self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        let mut machine = self;

        loop {
            machine = match machine {
                Self::Disconnected(supervisor) => supervisor.step(&shutdown).await?,
                Self::Connecting(supervisor) => supervisor.step(&shutdown).await?,
                Self::Connected(supervisor) => supervisor.step(&shutdown).await?,
                Self::Reconnecting(supervisor) => supervisor.step(&shutdown).await?,
                Self::Degraded(supervisor) => supervisor.step(&shutdown).await?,

                Self::ShuttingDown(supervisor) => {
                    supervisor.finish().await;
                    return Ok(());
                },
            };
        }
    }
}

#[derive(Debug)]
struct Supervisor<S> {
    core: MachineCore,
    state: S,
}

impl Supervisor<Disconnected> {
    fn initial(config: EnabledZenohConfig, io: RuntimeIo, publisher: RuntimePublisher) -> Self {
        Self {
            core: MachineCore {
                config,
                io,
                publisher,
                attempt: Attempt::ZERO,
                generation: Generation::ZERO,
                signals_closed: false,
            },
            state: Disconnected::now(),
        }
    }
}

impl<S> Supervisor<S> {
    fn enter<T>(self, state: T) -> Supervisor<T>
    where
        T: PublishState,
    {
        let Self { mut core, .. } = self;

        core.publisher.enter(&state);

        Supervisor { core, state }
    }
}

impl<S> Supervisor<S>
where
    S: IntoShuttingDown,
{
    fn into_shutdown_machine(self) -> Machine {
        let Self { mut core, state } = self;
        let state = state.into_shutting_down();

        core.publisher.enter(&state);

        Machine::ShuttingDown(Supervisor { core, state })
    }
}

impl<S> Supervisor<S>
where
    S: BackoffState + IntoShuttingDown,
{
    async fn run_backoff(mut self, shutdown: &ShutdownCoordinator) -> AppResult<Machine> {
        let sleep = time::sleep_until(self.state.next_attempt_at());
        tokio::pin!(sleep);

        let event = select! {
            biased;

            _ = shutdown.wait_for_shutdown() => BackoffEvent::Shutdown,

            signal = self.core.io.signals.recv(), if !self.core.signals_closed => {
                BackoffEvent::Signal(signal)
            }

            command = self.core.io.commands.recv() => {
                BackoffEvent::Command(command)
            }

            _ = &mut sleep => BackoffEvent::Retry,
        };

        match event {
            BackoffEvent::Shutdown => Ok(self.into_shutdown_machine()),

            BackoffEvent::Signal(Some(signal)) => {
                self.state.ignore_signal(signal);
                Ok(S::machine(self))
            },

            BackoffEvent::Signal(None) => {
                self.core.signals_closed = true;
                Ok(S::machine(self))
            },

            BackoffEvent::Command(Some(command)) => {
                self.core
                    .publisher
                    .record(CommandRequested::from(&command));
                command.reject_unavailable(self.state.unavailable_message());

                Ok(S::machine(self))
            },

            BackoffEvent::Command(None) => Ok(self.into_shutdown_machine()),

            BackoffEvent::Retry => {
                let attempt = self.core.next_attempt();
                let connecting = Connecting { attempt };

                Ok(Machine::Connecting(self.enter(connecting)))
            },
        }
    }
}

impl Supervisor<Disconnected> {
    async fn step(self, shutdown: &ShutdownCoordinator) -> AppResult<Machine> {
        self.run_backoff(shutdown).await
    }
}

impl Supervisor<Reconnecting> {
    async fn step(self, shutdown: &ShutdownCoordinator) -> AppResult<Machine> {
        self.run_backoff(shutdown).await
    }
}

impl Supervisor<Degraded> {
    async fn step(self, shutdown: &ShutdownCoordinator) -> AppResult<Machine> {
        self.run_backoff(shutdown).await
    }
}

impl Supervisor<Connecting> {
    async fn step(self, shutdown: &ShutdownCoordinator) -> AppResult<Machine> {
        let event = select! {
            biased;

            _ = shutdown.wait_for_shutdown() => ConnectingEvent::Shutdown,

            result = open_session(self.core.config.raw()) => {
                ConnectingEvent::Opened(result)
            }
        };

        match event {
            ConnectingEvent::Shutdown => Ok(self.into_shutdown_machine()),

            ConnectingEvent::Opened(Ok(session)) => {
                let attempt = self.state.attempt;
                let mut supervisor = self;
                let generation = supervisor.core.next_generation();

                let connected = Connected::new(ActiveSession::new(session, generation), attempt);

                Ok(Machine::Connected(supervisor.enter(connected)))
            },

            ConnectingEvent::Opened(Err(error)) => {
                let attempt = self.state.attempt;
                let retry_interval = self.core.config.retry_interval();

                let degraded = Degraded::after(attempt, error.to_string(), retry_interval);

                Ok(Machine::Degraded(self.enter(degraded)))
            },
        }
    }
}

impl Supervisor<Connected> {
    async fn step(mut self, shutdown: &ShutdownCoordinator) -> AppResult<Machine> {
        let event = select! {
            biased;

            _ = shutdown.wait_for_shutdown() => ConnectedEvent::Shutdown,

            signal = self.core.io.signals.recv(), if !self.core.signals_closed => {
                ConnectedEvent::Signal(signal)
            }

            command = self.core.io.commands.recv() => {
                ConnectedEvent::Command(command)
            }
        };

        match event {
            ConnectedEvent::Shutdown => Ok(self.into_shutdown_machine()),

            ConnectedEvent::Signal(Some(signal)) => match signal {
                SupervisorSignal::SessionFault { message } => self.into_reconnecting(message).await,
            },

            ConnectedEvent::Signal(None) => {
                self.core.signals_closed = true;
                Ok(Machine::Connected(self))
            },

            ConnectedEvent::Command(Some(command)) => {
                self.core
                    .publisher
                    .record(CommandRequested::from(&command));

                let reconnect_reason = command
                    .execute_connected(self.state.active.session())
                    .await;

                match reconnect_reason {
                    Some(reason) => self.into_reconnecting(reason).await,
                    None => Ok(Machine::Connected(self)),
                }
            },

            ConnectedEvent::Command(None) => Ok(self.into_shutdown_machine()),
        }
    }

    async fn into_reconnecting(self, reason: String) -> AppResult<Machine> {
        let Supervisor { mut core, state } = self;

        let retry_interval = core.config.retry_interval();
        let (active, transitioned) = state.into_reconnecting(reason, retry_interval);
        let reconnecting = transitioned.into_state();

        core.publisher.enter(&reconnecting);

        active.close().await;

        Ok(Machine::Reconnecting(Supervisor { core, state: reconnecting }))
    }
}

impl Supervisor<ShuttingDown> {
    async fn finish(self) {
        if let Some(active) = self.state.active {
            active.close().await;
        }
    }
}

#[derive(Debug)]
enum BackoffEvent {
    Shutdown,
    Signal(Option<SupervisorSignal>),
    Command(Option<Command>),
    Retry,
}

#[derive(Debug)]
enum ConnectingEvent {
    Shutdown,
    Opened(AppResult<Session>),
}

#[derive(Debug)]
enum ConnectedEvent {
    Shutdown,
    Signal(Option<SupervisorSignal>),
    Command(Option<Command>),
}

// === Machine core ============================================================

#[derive(Debug)]
struct MachineCore {
    config: EnabledZenohConfig,
    io: RuntimeIo,
    publisher: RuntimePublisher,
    attempt: Attempt,
    generation: Generation,
    signals_closed: bool,
}

impl MachineCore {
    fn next_attempt(&mut self) -> Attempt {
        self.attempt = self.attempt.next();
        self.attempt
    }

    fn next_generation(&mut self) -> Generation {
        self.generation = self.generation.next();
        self.generation
    }
}

#[derive(Debug)]
struct RuntimeIo {
    commands: mpsc::Receiver<Command>,
    signals: mpsc::Receiver<SupervisorSignal>,
}

// === Runtime publication =====================================================
//
// This is intentionally dumb. It cannot invent lifecycle transitions.
// It can only publish typed lifecycle states that implement PublishState,
// or typed non-state records that implement EventRecord.

#[derive(Debug)]
struct RuntimePublisher {
    status: watch::Sender<ZenohStatus>,
    session: watch::Sender<Option<SessionLease>>,
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
            self.send_event(event);
        }

        publication
            .log
            .emit(self.clock.take_elapsed_ms());
    }

    fn record<E>(&self, event: E)
    where
        E: EventRecord,
    {
        self.send_event(event.into_event());
    }

    fn send_event(&self, event: ZenohEvent) {
        if let Err(error) = self.events.send(event) {
            tracing::trace!(event = %error.0, "dropping Zenoh event without subscribers");
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
    Connected(SessionLease),
}

impl SessionSnapshot {
    fn into_option(self) -> Option<SessionLease> {
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
    OpenFailed { attempt: Attempt, error: String },
    SessionLost { attempt: Attempt, error: String },
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
                    "zenoh connection attempt started"
                );
            },

            Self::Reconnecting { attempt } => {
                tracing::info!(
                    component = "zenoh",
                    state = "reconnecting",
                    attempt = u64::from(attempt),
                    state_elapsed_ms,
                    "zenoh reconnection attempt started"
                );
            },

            Self::Connected { attempt, zid } => {
                tracing::info!(
                    component = "zenoh",
                    state = "connected",
                    attempt = u64::from(attempt),
                    zid,
                    state_elapsed_ms,
                    "zenoh session established"
                );
            },

            Self::OpenFailed { attempt, error } => {
                tracing::warn!(
                    component = "zenoh",
                    state = "degraded",
                    attempt = u64::from(attempt),
                    error,
                    state_elapsed_ms,
                    "zenoh session open failed"
                );
            },

            Self::SessionLost { attempt, error } => {
                tracing::warn!(
                    component = "zenoh",
                    state = "reconnecting",
                    attempt = u64::from(attempt),
                    error,
                    state_elapsed_ms,
                    "zenoh session lost"
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

// === Sealed lifecycle/event traits ==========================================

mod sealed {
    pub trait Sealed {}
}

trait PublishState: sealed::Sealed {
    fn publication(&self) -> Publication;
}

trait EventRecord: sealed::Sealed {
    fn into_event(self) -> ZenohEvent;
}

trait IntoShuttingDown: sealed::Sealed + Sized {
    fn into_shutting_down(self) -> ShuttingDown;
}

trait BackoffState: sealed::Sealed + Sized {
    const STATE_NAME: &'static str;

    fn next_attempt_at(&self) -> time::Instant;

    fn unavailable_message(&self) -> &'static str {
        "Zenoh session is not connected."
    }

    fn ignore_signal(&self, signal: SupervisorSignal) {
        match signal {
            SupervisorSignal::SessionFault { message } => {
                tracing::trace!(
                    component = "zenoh",
                    state = Self::STATE_NAME,
                    error = %message,
                    "ignoring Zenoh session fault without an active session"
                );
            },
        }
    }

    fn machine(supervisor: Supervisor<Self>) -> Machine;
}

// === Typed lifecycle states ==================================================

#[derive(Debug)]
struct Disabled;

impl sealed::Sealed for Disabled {}

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
struct Disconnected {
    next_attempt_at: time::Instant,
}

impl Disconnected {
    fn now() -> Self {
        Self { next_attempt_at: time::Instant::now() }
    }
}

impl sealed::Sealed for Disconnected {}

impl BackoffState for Disconnected {
    const STATE_NAME: &'static str = "disconnected";

    fn next_attempt_at(&self) -> time::Instant {
        self.next_attempt_at
    }

    fn machine(supervisor: Supervisor<Self>) -> Machine {
        Machine::Disconnected(supervisor)
    }
}

impl IntoShuttingDown for Disconnected {
    fn into_shutting_down(self) -> ShuttingDown {
        ShuttingDown::without_active()
    }
}

#[derive(Debug)]
struct Connecting {
    attempt: Attempt,
}

impl sealed::Sealed for Connecting {}

impl PublishState for Connecting {
    fn publication(&self) -> Publication {
        if self.attempt.is_first() {
            return Publication {
                status: ZenohStatus::starting(self.attempt.into()),
                session: SessionSnapshot::Disconnected,
                event: Some(ZenohEvent::Connecting { attempt: self.attempt.into() }),
                log: LogRecord::Connecting { attempt: self.attempt },
            };
        }

        let message = "Retrying Zenoh session establishment.".to_owned();

        Publication {
            status: ZenohStatus::reconnecting(self.attempt.into(), message.clone()),
            session: SessionSnapshot::Disconnected,
            event: Some(ZenohEvent::Reconnecting { attempt: self.attempt.into(), message }),
            log: LogRecord::Reconnecting { attempt: self.attempt },
        }
    }
}

impl IntoShuttingDown for Connecting {
    fn into_shutting_down(self) -> ShuttingDown {
        ShuttingDown::without_active()
    }
}

#[derive(Debug)]
struct Connected {
    active: ActiveSession,
    attempt: Attempt,
}

impl Connected {
    fn new(active: ActiveSession, attempt: Attempt) -> Self {
        Self { active, attempt }
    }

    fn into_reconnecting(
        self,
        reason: String,
        retry_interval: Duration,
    ) -> (ActiveSession, Transitioned<Reconnecting>) {
        let reconnecting = Reconnecting::after(self.attempt, reason, retry_interval);

        (self.active, Transitioned::new(reconnecting))
    }
}

impl sealed::Sealed for Connected {}

impl PublishState for Connected {
    fn publication(&self) -> Publication {
        let zid = self.active.zid();

        Publication {
            status: ZenohStatus::connected(self.attempt.into()),
            session: SessionSnapshot::Connected(self.active.lease()),
            event: Some(ZenohEvent::Connected { attempt: self.attempt.into() }),
            log: LogRecord::Connected { attempt: self.attempt, zid },
        }
    }
}

impl IntoShuttingDown for Connected {
    fn into_shutting_down(self) -> ShuttingDown {
        ShuttingDown::with_active(self.active)
    }
}

#[derive(Debug)]
struct Reconnecting {
    attempt: Attempt,
    reason: String,
    next_attempt_at: time::Instant,
}

impl Reconnecting {
    fn after(attempt: Attempt, reason: String, delay: Duration) -> Self {
        Self { attempt, reason, next_attempt_at: time::Instant::now() + delay }
    }
}

impl sealed::Sealed for Reconnecting {}

impl PublishState for Reconnecting {
    fn publication(&self) -> Publication {
        Publication {
            status: ZenohStatus::reconnecting(self.attempt.into(), self.reason.clone()),
            session: SessionSnapshot::Disconnected,
            event: Some(ZenohEvent::Reconnecting {
                attempt: self.attempt.into(),
                message: self.reason.clone(),
            }),
            log: LogRecord::SessionLost { attempt: self.attempt, error: self.reason.clone() },
        }
    }
}

impl BackoffState for Reconnecting {
    const STATE_NAME: &'static str = "reconnecting";

    fn next_attempt_at(&self) -> time::Instant {
        self.next_attempt_at
    }

    fn machine(supervisor: Supervisor<Self>) -> Machine {
        Machine::Reconnecting(supervisor)
    }
}

impl IntoShuttingDown for Reconnecting {
    fn into_shutting_down(self) -> ShuttingDown {
        ShuttingDown::without_active()
    }
}

#[derive(Debug)]
struct Degraded {
    attempt: Attempt,
    error: String,
    next_attempt_at: time::Instant,
}

impl Degraded {
    fn after(attempt: Attempt, error: String, delay: Duration) -> Self {
        Self { attempt, error, next_attempt_at: time::Instant::now() + delay }
    }
}

impl sealed::Sealed for Degraded {}

impl PublishState for Degraded {
    fn publication(&self) -> Publication {
        Publication {
            status: ZenohStatus::degraded(self.attempt.into(), self.error.clone()),
            session: SessionSnapshot::Disconnected,
            event: Some(ZenohEvent::Failed {
                attempt: self.attempt.into(),
                message: self.error.clone(),
            }),
            log: LogRecord::OpenFailed { attempt: self.attempt, error: self.error.clone() },
        }
    }
}

impl BackoffState for Degraded {
    const STATE_NAME: &'static str = "degraded";

    fn next_attempt_at(&self) -> time::Instant {
        self.next_attempt_at
    }

    fn machine(supervisor: Supervisor<Self>) -> Machine {
        Machine::Degraded(supervisor)
    }
}

impl IntoShuttingDown for Degraded {
    fn into_shutting_down(self) -> ShuttingDown {
        ShuttingDown::without_active()
    }
}

#[derive(Debug)]
struct ShuttingDown {
    active: Option<ActiveSession>,
}

impl ShuttingDown {
    fn without_active() -> Self {
        Self { active: None }
    }

    fn with_active(active: ActiveSession) -> Self {
        Self { active: Some(active) }
    }
}

impl sealed::Sealed for ShuttingDown {}

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

// === Linear-ish active session owner ========================================

#[derive(Debug)]
struct ActiveSession {
    session: Session,
    generation: Generation,
}

impl ActiveSession {
    fn new(session: Session, generation: Generation) -> Self {
        Self { session, generation }
    }

    fn session(&self) -> &Session {
        &self.session
    }

    fn zid(&self) -> String {
        self.session.zid().to_string()
    }

    fn lease(&self) -> SessionLease {
        let zid = self.zid();

        SessionLease::new(self.session.clone(), zid, self.generation.into())
    }

    async fn close(self) {
        close_session(self.session).await;
    }
}

// === Must-use transition wrapper ============================================

#[must_use = "state transitions must be installed back into the machine"]
#[derive(Debug)]
struct Transitioned<S> {
    state: S,
}

impl<S> Transitioned<S> {
    fn new(state: S) -> Self {
        Self { state }
    }

    fn into_state(self) -> S {
        self.state
    }
}

// === Typed non-state runtime events =========================================

#[derive(Debug)]
struct CommandRequested {
    event: ZenohEvent,
}

impl From<&Command> for CommandRequested {
    fn from(command: &Command) -> Self {
        let event = match command {
            Command::Publish { key, payload, .. } => {
                ZenohEvent::PublishRequested { bytes: payload.len(), key: key.clone() }
            },

            Command::Delete { key, .. } => ZenohEvent::DeleteRequested { key: key.clone() },
        };

        Self { event }
    }
}

impl sealed::Sealed for CommandRequested {}

impl EventRecord for CommandRequested {
    fn into_event(self) -> ZenohEvent {
        self.event
    }
}

// === Domain counters =========================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct Attempt(u64);

impl Attempt {
    const ZERO: Self = Self(0);

    fn next(self) -> Self {
        Self(self.0.saturating_add(1))
    }

    fn is_first(self) -> bool {
        self.0 == 1
    }
}

impl From<Attempt> for u64 {
    fn from(attempt: Attempt) -> Self {
        attempt.0
    }
}

impl From<u64> for Attempt {
    fn from(value: u64) -> Self {
        Self(value)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct Generation(u64);

impl Generation {
    const ZERO: Self = Self(0);

    fn next(self) -> Self {
        Self(self.0.saturating_add(1))
    }
}

impl From<Generation> for u64 {
    fn from(generation: Generation) -> Self {
        generation.0
    }
}

impl From<Generation> for String {
    fn from(generation: Generation) -> Self {
        generation.0.to_string()
    }
}

// === Command behavior ========================================================

impl Command {
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

    async fn execute_connected(self, session: &Session) -> Option<String> {
        match self {
            Self::Publish { key, payload, encoding, attachment, respond_to } => {
                let response = publish(session, &key, payload, encoding, attachment).await;
                let reconnect_reason = response
                    .as_ref()
                    .err()
                    .map(ToString::to_string);

                let _ = respond_to.send(response);

                reconnect_reason
            },

            Self::Delete { key, respond_to } => {
                let response = delete(session, &key).await;
                let reconnect_reason = response
                    .as_ref()
                    .err()
                    .map(ToString::to_string);

                let _ = respond_to.send(response);

                reconnect_reason
            },
        }
    }
}
