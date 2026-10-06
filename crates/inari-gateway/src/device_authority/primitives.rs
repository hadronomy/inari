use std::fmt;

use chrono::{DateTime, Datelike, SecondsFormat, Utc};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};

use crate::{GatewayError, GatewayResult};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(transparent)]
pub struct Identifier(String);

impl Identifier {
    pub fn new(value: String) -> GatewayResult<Self> {
        if value.trim().is_empty() || value.chars().count() > 256 {
            return Err(GatewayError::InvalidInput(
                "authority identifiers need 1 to 256 characters".into(),
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for Identifier {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::new(String::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(transparent)]
pub struct AuthorityDigest(String);

impl AuthorityDigest {
    #[must_use]
    pub fn of(bytes: &[u8]) -> Self {
        Self(hex::encode(Sha256::digest(bytes)))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for AuthorityDigest {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        if value.len() != 64
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(serde::de::Error::custom("authority digests need 64 lowercase hex digits"));
        }
        Ok(Self(value))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(transparent)]
pub struct PositiveInteger(u64);

impl PositiveInteger {
    pub fn new(value: u64) -> GatewayResult<Self> {
        if !(1..=9_007_199_254_740_991).contains(&value) {
            return Err(GatewayError::InvalidInput(
                "authority integers must be positive and exact in RFC 8785".into(),
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn get(self) -> u64 {
        self.0
    }
}

impl<'de> Deserialize<'de> for PositiveInteger {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::new(u64::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct AuthorityTime(DateTime<Utc>);

impl AuthorityTime {
    pub fn new(value: DateTime<Utc>) -> GatewayResult<Self> {
        if !(1..=9999).contains(&value.year())
            || !value
                .timestamp_subsec_nanos()
                .is_multiple_of(1_000)
            || value.timestamp_subsec_nanos() >= 1_000_000_000
        {
            return Err(GatewayError::InvalidInput(
                "authority timestamps need microsecond precision without leap seconds".into(),
            ));
        }
        Ok(Self(value))
    }

    #[must_use]
    pub fn get(self) -> DateTime<Utc> {
        self.0
    }

    pub(super) fn signing_value(self) -> serde_json::Value {
        self.0
            .to_rfc3339_opts(SecondsFormat::Micros, true)
            .into()
    }
}

impl Serialize for AuthorityTime {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        // Pydantic omits the fractional part only when every microsecond is zero.
        let format = if self.0.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        };
        serializer.serialize_str(&self.0.to_rfc3339_opts(format, true))
    }
}

impl<'de> Deserialize<'de> for AuthorityTime {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        let parsed = DateTime::parse_from_rfc3339(&value).map_err(serde::de::Error::custom)?;
        if parsed.offset().local_minus_utc() != 0 {
            return Err(serde::de::Error::custom("authority timestamps must use UTC"));
        }
        Self::new(parsed.with_timezone(&Utc)).map_err(serde::de::Error::custom)
    }
}

#[derive(Clone, PartialEq, Eq)]
pub struct HexBytes<const LENGTH: usize>([u8; LENGTH]);

impl<const LENGTH: usize> HexBytes<LENGTH> {
    #[must_use]
    pub fn new(bytes: [u8; LENGTH]) -> Self {
        Self(bytes)
    }

    #[must_use]
    pub fn as_bytes(&self) -> &[u8; LENGTH] {
        &self.0
    }
}

impl<const LENGTH: usize> fmt::Debug for HexBytes<LENGTH> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_tuple("HexBytes")
            .field(&hex::encode(self.0))
            .finish()
    }
}

impl<const LENGTH: usize> Serialize for HexBytes<LENGTH> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&hex::encode(self.0))
    }
}

impl<'de, const LENGTH: usize> Deserialize<'de> for HexBytes<LENGTH> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        let mut bytes = [0; LENGTH];
        hex::decode_to_slice(value, &mut bytes).map_err(serde::de::Error::custom)?;
        Ok(Self(bytes))
    }
}
