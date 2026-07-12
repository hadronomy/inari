use std::fmt;
use std::str::FromStr;

use data_encoding::BASE32_NOPAD;
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};

use crate::{GatewayError, GatewayResult};

const ID_LENGTH: usize = 12;
const SECRET_LENGTH: usize = 32;
const PREFIX: &str = "INR";
const ALPHABET: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct InvitationId(String);

impl InvitationId {
    pub fn generate() -> GatewayResult<Self> {
        let mut entropy = [0_u8; 8];
        getrandom::fill(&mut entropy).map_err(|error| {
            GatewayError::Unavailable(format!("secure random generator unavailable: {error}"))
        })?;
        let encoded = BASE32_NOPAD.encode(&entropy);
        Self::from_str(&encoded[..ID_LENGTH])
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for InvitationId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl Serialize for InvitationId {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for InvitationId {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        String::deserialize(deserializer)?
            .parse()
            .map_err(serde::de::Error::custom)
    }
}

impl FromStr for InvitationId {
    type Err = GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let normalized = value.trim().to_ascii_uppercase();
        if normalized.len() != ID_LENGTH
            || !normalized
                .bytes()
                .all(|byte| byte.is_ascii() && ALPHABET.as_bytes().contains(&byte))
        {
            return Err(GatewayError::InvalidInput(
                "invitation id must contain 12 RFC 4648 base32 characters".into(),
            ));
        }
        Ok(Self(normalized))
    }
}

#[derive(Clone)]
pub struct InvitationSecret(SecretString);

impl InvitationSecret {
    pub fn generate() -> GatewayResult<Self> {
        let mut entropy = [0_u8; 20];
        getrandom::fill(&mut entropy).map_err(|error| {
            GatewayError::Unavailable(format!("secure random generator unavailable: {error}"))
        })?;
        Self::from_str(&BASE32_NOPAD.encode(&entropy))
    }

    #[must_use]
    pub fn expose(&self) -> &str {
        self.0.expose_secret()
    }
}

impl fmt::Debug for InvitationSecret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("InvitationSecret([REDACTED])")
    }
}

impl FromStr for InvitationSecret {
    type Err = GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let normalized = value.trim().to_ascii_uppercase();
        if normalized.len() != SECRET_LENGTH
            || !normalized
                .bytes()
                .all(|byte| byte.is_ascii() && ALPHABET.as_bytes().contains(&byte))
        {
            return Err(GatewayError::InvalidInput(
                "invitation secret must contain 32 RFC 4648 base32 characters".into(),
            ));
        }
        Ok(Self(SecretString::from(normalized)))
    }
}

#[derive(Clone, Debug)]
pub struct InvitationCode {
    id: InvitationId,
    secret: InvitationSecret,
}

impl InvitationCode {
    pub fn generate() -> GatewayResult<Self> {
        Ok(Self { id: InvitationId::generate()?, secret: InvitationSecret::generate()? })
    }

    #[must_use]
    pub fn id(&self) -> &InvitationId {
        &self.id
    }

    #[must_use]
    pub fn secret_digest(&self) -> [u8; 32] {
        Sha256::digest(self.normalized().as_bytes()).into()
    }

    #[must_use]
    pub fn normalized(&self) -> String {
        format!("{PREFIX}{}{}", self.id, self.secret.expose())
    }

    #[must_use]
    pub fn manual(&self) -> String {
        let groups = self
            .secret
            .expose()
            .as_bytes()
            .chunks(4)
            .map(|chunk| std::str::from_utf8(chunk).expect("base32 is ASCII"))
            .collect::<Vec<_>>()
            .join("-");
        format!("{PREFIX}-{}-{groups}", self.id)
    }
}

impl FromStr for InvitationCode {
    type Err = GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        if !value.is_ascii() {
            return Err(GatewayError::InvalidInput("invitation code must be ASCII".into()));
        }
        let normalized = value
            .bytes()
            .filter(|byte| !byte.is_ascii_whitespace() && *byte != b'-')
            .map(|byte| byte.to_ascii_uppercase())
            .collect::<Vec<_>>();
        let normalized = std::str::from_utf8(&normalized)
            .map_err(|_| GatewayError::InvalidInput("invitation code must be ASCII".into()))?;
        let body = normalized
            .strip_prefix(PREFIX)
            .ok_or_else(|| GatewayError::InvalidInput("invalid invitation code prefix".into()))?;
        if body.len() != ID_LENGTH + SECRET_LENGTH {
            return Err(GatewayError::InvalidInput("invalid invitation code length".into()));
        }
        let (id, secret) = body.split_at(ID_LENGTH);
        Ok(Self { id: InvitationId::from_str(id)?, secret: InvitationSecret::from_str(secret)? })
    }
}

#[cfg(test)]
mod tests {
    use super::InvitationCode;
    use std::str::FromStr;

    #[test]
    fn generated_code_round_trips() {
        let code = InvitationCode::generate().expect("code generation should succeed");
        let parsed = InvitationCode::from_str(&code.manual()).expect("generated code should parse");
        assert_eq!(parsed.normalized(), code.normalized());
        assert_eq!(parsed.secret_digest(), code.secret_digest());
    }

    #[test]
    fn unicode_input_is_rejected_without_panicking() {
        assert!(InvitationCode::from_str("INR-🦀").is_err());
    }
}
