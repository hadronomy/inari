use tokio::{select, time};
use zenoh::Session;

use super::attempt::{Attempt, AttemptCounter};
use super::publish::RuntimePublisher;
use super::state::{Degraded, Opening, Ready, ShuttingDownState, State};
use super::{EnabledZenohConfig, RuntimeIo, SupervisorSignal};
use crate::error::AppResult;
use crate::shutdown::ShutdownCoordinator;
use crate::zenoh::command::Command;
use crate::zenoh::session::{close_session, open_session};
use crate::zenoh::{CurrentSession, SessionGeneration};

#[derive(Debug)]
pub(super) struct EnabledSupervisor {
    config: EnabledZenohConfig,
    io: RuntimeIo,
    publisher: RuntimePublisher,
    attempts: AttemptCounter,
    generation: SessionGeneration,
    signals_closed: bool,
}

impl EnabledSupervisor {
    pub(super) fn initial(
        config: EnabledZenohConfig,
        io: RuntimeIo,
        publisher: RuntimePublisher,
    ) -> Self {
        Self {
            config,
            io,
            publisher,
            attempts: AttemptCounter::new(),
            generation: SessionGeneration::ZERO,
            signals_closed: false,
        }
    }

    pub(super) async fn run(mut self, shutdown: ShutdownCoordinator) -> AppResult<()> {
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
                .unwrap_or_else(|| "Zenoh session is closed.".into());

            return self.enter(State::Degraded(Degraded::after(
                ready.attempt,
                message,
                self.config.retry_interval(),
            )));
        }

        State::Ready(ready)
    }

    fn enter(&mut self, state: State) -> State {
        self.publisher.enter_state(&state);
        state
    }

    fn next_attempt(&mut self) -> Attempt {
        self.attempts.next()
    }

    fn next_generation(&mut self) -> SessionGeneration {
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
