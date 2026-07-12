#![forbid(unsafe_code)]

pub mod audit;
pub mod certificate;
mod error;
pub mod identity;
pub mod onboarding;
pub mod persistence;
pub mod protocol;
pub mod security;

pub use error::{GatewayError, GatewayResult};
pub use persistence::{
    AgentEnrollmentRecord, GatewayRepository, PersistedAgentStatus, PersistedCommand,
    PersistedPublication,
};
