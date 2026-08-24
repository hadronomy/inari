mod logging;
pub mod platform;
mod runtime;
mod tray;

pub use logging::initialize_logging;
pub(crate) use runtime::agent_failure_message;
pub use runtime::{AgentRuntime, AgentRuntimeUpdate, SetupResult};
pub use tray::{TrayCommand, TrayController};
