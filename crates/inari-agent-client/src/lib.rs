//! Typed access to the local Inari agent.
//!
//! The generated OpenAPI client is deliberately private. Desktop features use
//! the curated domain vocabulary exported by this crate, keeping transport
//! details and generated names out of application state.

mod client;
mod error;
mod events;
mod identity;
mod model;
mod pairing;
mod service;
mod transport;

pub use client::{AgentClient, AgentClientOptions};
pub use error::{AgentClientError, AgentClientResult};
pub use events::{AgentEvent, AgentEventKind, AgentEventStream, EventResource};
pub use identity::{ClientIdentity, IdentityStore, LocalIdentityStore};
pub use model::{
    AgentConnection, Device, DeviceId, DeviceKind, DeviceState, EnrollmentPreview, InvitationLink,
    Job, JobId, JobState, ServiceState, SetupAccess, SetupSnapshot, SetupStage,
};
pub use pairing::PairingMode;
pub use service::{LocalAgentService, ServiceControlError, ServiceControlResult, ServiceOperation};
