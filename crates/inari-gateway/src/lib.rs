#![forbid(unsafe_code)]

pub mod credentials;
mod error;
pub mod onboarding;
pub mod persistence;
pub mod protocol;
pub mod security;

pub use error::{GatewayError, GatewayResult};
pub use persistence::{
    AgentEnrollmentRecord, EnrollmentCredential, GatewayRepository, PersistedAgentStatus,
    PersistedCommand, PersistedPublication,
};
