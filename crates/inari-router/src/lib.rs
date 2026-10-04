//! Signed, expiring Router policy and exact Agent namespace admission.
//!
//! A verified policy is the only input that can generate an active Zenoh ACL.

mod acl;
mod error;
mod policy;
mod store;
mod tls;

pub mod management;
pub mod supervisor;

pub use acl::RouterConfig;
pub use error::{RouterError, RouterResult};
pub use policy::{AgentAdmission, Policy, SignedPolicy, VerifiedPolicy};
pub use store::PolicyStore;

#[cfg(test)]
mod tests;
