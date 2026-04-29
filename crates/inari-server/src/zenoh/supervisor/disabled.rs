use tokio::select;

use super::publish::RuntimePublisher;
use super::{RuntimeIo, SupervisorSignal};
use crate::error::AppResult;
use crate::shutdown::ShutdownCoordinator;
use crate::zenoh::command::Command;

#[derive(Debug)]
pub(super) struct DisabledSupervisor {
    io: RuntimeIo,
    publisher: RuntimePublisher,
}

impl DisabledSupervisor {
    pub(super) fn new(io: RuntimeIo, publisher: RuntimePublisher) -> Self {
        Self { io, publisher }
    }

    pub(super) async fn run(mut self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        self.publisher.enter_disabled();

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

        self.publisher.enter_shutting_down();

        Ok(())
    }
}

#[derive(Debug)]
enum DisabledEvent {
    Shutdown,
    Signal(Option<SupervisorSignal>),
    Command(Option<Command>),
}
