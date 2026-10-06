//! Typed Device authority records and their Agent-compatible signatures.

mod canonical;
mod models;
mod primitives;

pub use canonical::{CanonicalRecord, ControllerRecordPayload, RecordPayload};
pub use models::*;
pub use primitives::{AuthorityDigest, AuthorityTime, HexBytes, Identifier, PositiveInteger};
