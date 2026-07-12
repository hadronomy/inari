use std::fmt;
use std::str::FromStr;

use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

use crate::GatewayError;

const SHA256_BYTES: usize = 32;

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct TokenDigest([u8; SHA256_BYTES]);

impl TokenDigest {
    fn matches(self, candidate: &[u8; SHA256_BYTES]) -> bool {
        bool::from(self.0.ct_eq(candidate))
    }
}

impl fmt::Debug for TokenDigest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_tuple("TokenDigest")
            .field(&"[REDACTED]")
            .finish()
    }
}

impl fmt::Display for TokenDigest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&hex::encode(self.0))
    }
}

impl FromStr for TokenDigest {
    type Err = GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let normalized = value
            .bytes()
            .filter(|byte| !byte.is_ascii_whitespace() && *byte != b':')
            .collect::<Vec<_>>();
        let mut digest = [0_u8; SHA256_BYTES];
        hex::decode_to_slice(normalized, &mut digest).map_err(|_| {
            GatewayError::InvalidInput(
                "token hashes must contain exactly 64 hexadecimal digits".into(),
            )
        })?;
        Ok(Self(digest))
    }
}

impl Serialize for TokenDigest {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&self.to_string())
    }
}

impl<'de> Deserialize<'de> for TokenDigest {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        String::deserialize(deserializer)?
            .parse()
            .map_err(serde::de::Error::custom)
    }
}

#[derive(Clone, Debug, Default)]
pub struct TokenVerifier {
    digests: Vec<TokenDigest>,
}

impl TokenVerifier {
    #[must_use]
    pub fn new(digests: Vec<TokenDigest>) -> Self {
        Self { digests }
    }

    #[must_use]
    pub fn is_configured(&self) -> bool {
        !self.digests.is_empty()
    }

    #[must_use]
    pub fn accepts(&self, token: &SecretString) -> bool {
        let candidate: [u8; SHA256_BYTES] = Sha256::digest(token.expose_secret().as_bytes()).into();
        self.digests
            .iter()
            .fold(false, |accepted, digest| digest.matches(&candidate) | accepted)
    }
}

#[cfg(test)]
mod tests {
    use secrecy::SecretString;

    use super::{TokenDigest, TokenVerifier};

    #[test]
    fn verifies_tokens_without_exposing_the_configured_digest() {
        let digest = "0850123315d21ab90f4f7236408a52ef6dbd6a02a6550e5c10dc73f4d993680e"
            .parse::<TokenDigest>()
            .expect("digest should parse");
        let verifier = TokenVerifier::new(vec![digest]);

        assert!(verifier.accepts(&SecretString::from("operator-token")));
        assert!(!verifier.accepts(&SecretString::from("another-token")));
        assert_eq!(format!("{digest:?}"), "TokenDigest(\"[REDACTED]\")");
    }

    #[test]
    fn rejects_malformed_digests() {
        assert!(
            "not-a-sha256-digest"
                .parse::<TokenDigest>()
                .is_err()
        );
    }
}
