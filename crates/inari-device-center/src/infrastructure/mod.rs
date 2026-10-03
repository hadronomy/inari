mod logging;
pub mod platform;
mod runtime;
mod setup;
mod tray;

pub use logging::{initialize_logging, log_directory};
pub(crate) use runtime::agent_failure_message;
pub use runtime::{AgentRuntime, AgentRuntimeUpdate, SetupResult};
pub(crate) use setup::pending as setup_progress_pending;
pub(crate) use setup::{SetupProgressError, SetupProgressMode};
pub use tray::{TrayCommand, TrayController};
