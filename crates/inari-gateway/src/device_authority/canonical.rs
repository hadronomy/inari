use serde::Serialize;
use serde_json::Value;

use super::{AuthorityDigest, AuthorityTime, SignerPurpose};
use crate::{GatewayError, GatewayResult};

pub(super) mod sealed {
    pub trait Record {}
    pub trait Controller {}
}

/// Only the six protocol records can produce a signed authority payload.
pub trait RecordPayload: sealed::Record + Serialize + Clone {
    const PURPOSE: SignerPurpose;

    fn validate(&self) -> GatewayResult<()>;
    fn signing_value(&self) -> GatewayResult<Value>;
}

/// The Controller cannot sign Agent observation or Device Test evidence.
///
/// ```compile_fail
/// use inari_gateway::device_authority::{ControllerRecordPayload, DeviceTestEvidence};
/// fn controller_record<T: ControllerRecordPayload>() {}
/// controller_record::<DeviceTestEvidence>();
/// ```
pub trait ControllerRecordPayload: RecordPayload + sealed::Controller {}

/// Validated record and the immutable bytes that its signature covers.
#[derive(Debug, Clone)]
pub struct CanonicalRecord<T: RecordPayload> {
    pub(super) record: T,
    pub(super) digest: AuthorityDigest,
    bytes: Vec<u8>,
}

impl<T: RecordPayload> CanonicalRecord<T> {
    /// Reject invalid fields before computing the RFC 8785 payload and SHA-256 digest.
    pub fn new(record: T) -> GatewayResult<Self> {
        record.validate()?;
        let bytes = serde_json_canonicalizer::to_vec(&record.signing_value()?)?;
        let digest = AuthorityDigest::of(&bytes);
        Ok(Self { record, digest, bytes })
    }

    #[must_use]
    pub fn record(&self) -> &T {
        &self.record
    }

    #[must_use]
    pub fn digest(&self) -> &AuthorityDigest {
        &self.digest
    }

    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }
}

pub(super) fn timestamp(value: Option<AuthorityTime>) -> Value {
    value.map_or(Value::Null, AuthorityTime::signing_value)
}

pub(super) fn replace_times<T: Serialize>(
    record: &T,
    timestamps: &[(&str, Option<AuthorityTime>)],
) -> GatewayResult<Value> {
    let Value::Object(mut object) = serde_json::to_value(record)? else {
        return Err(GatewayError::InvalidInput("an authority record must be an object".into()));
    };
    for (name, value) in timestamps {
        object.insert((*name).into(), timestamp(*value));
    }
    Ok(Value::Object(object))
}

pub(super) fn invalid(message: &str) -> GatewayError {
    GatewayError::InvalidInput(message.into())
}

pub(super) fn validity(start: AuthorityTime, end: Option<AuthorityTime>) -> GatewayResult<()> {
    if end.is_some_and(|end| end <= start) {
        return Err(invalid("authority expiry must follow its effective time"));
    }
    Ok(())
}
