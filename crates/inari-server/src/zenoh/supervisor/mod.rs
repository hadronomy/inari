mod attempt;
mod disabled;
mod enabled;
mod publish;
mod state;

use std::time::Duration;

use tokio::sync::{broadcast, mpsc, watch};

use super::command::Command;
use super::handle::ZenohHandle;
use super::{CurrentSession, ZenohStatus};
use crate::config::ZenohConfig;
use crate::error::AppResult;
use crate::shutdown::ShutdownCoordinator;
use disabled::DisabledSupervisor;
use enabled::EnabledSupervisor;
use publish::RuntimePublisher;

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

        let (commands_tx, commands) = mpsc::channel(command_buffer.into());
        let (signals_tx, signals) = mpsc::channel(32);
        let (status, status_rx) = watch::channel(initial_status);
        let (session, session_rx) = watch::channel(None::<CurrentSession>);
        let (events, _) = broadcast::channel(event_buffer.into());

        let handle =
            ZenohHandle::new(commands_tx, signals_tx, status_rx, session_rx, events.clone());

        let supervisor = Self {
            mode: SupervisorMode::from(config),
            io: RuntimeIo { commands, signals },
            publisher: RuntimePublisher::new(status, session, events),
        };

        (handle, supervisor)
    }

    pub async fn run(self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        let Self { mode, io, publisher } = self;

        match mode {
            SupervisorMode::Disabled => {
                DisabledSupervisor::new(io, publisher)
                    .run(shutdown)
                    .await
            },
            SupervisorMode::Enabled(config) => {
                EnabledSupervisor::initial(*config, io, publisher)
                    .run(shutdown)
                    .await
            },
        }
    }
}

#[derive(Debug)]
enum SupervisorMode {
    Disabled,
    Enabled(Box<EnabledZenohConfig>),
}

impl From<ZenohConfig> for SupervisorMode {
    fn from(config: ZenohConfig) -> Self {
        if config.enabled {
            Self::Enabled(Box::new(EnabledZenohConfig::new(config)))
        } else {
            Self::Disabled
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

#[derive(Debug)]
struct RuntimeIo {
    commands: mpsc::Receiver<Command>,
    signals: mpsc::Receiver<SupervisorSignal>,
}
