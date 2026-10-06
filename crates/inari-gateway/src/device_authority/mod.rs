//! Typed Device authority records and their Agent-compatible signatures.

mod canonical;
mod models;
mod primitives;

pub use canonical::{CanonicalRecord, ControllerRecordPayload, RecordPayload};
pub use models::{
    AuthorityBundle, AuthorityContract, AuthorityManifest, AuthorityRevision, AuthorityScope,
    BindingActivation, BindingRevision, DeviceCapability, DeviceObservation, DeviceTestEvidence,
    DeviceTestResult, DriverProfile, HardwareCertificationMatrixRow, OutputEvidence, ScopeKind,
    SignedAuthorityRevision, SignedBindingRevision, SignedDeviceObservation,
    SignedDeviceTestEvidence, SignedDriverProfile, SignedHardwareCertificationMatrixRow,
    SignerPurpose, SignerRecord, SignerState,
};
pub use primitives::{AuthorityDigest, AuthorityTime, HexBytes, Identifier, PositiveInteger};
