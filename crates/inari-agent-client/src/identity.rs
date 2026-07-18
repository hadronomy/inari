use ed25519_dalek::{
    SigningKey, VerifyingKey,
    pkcs8::{DecodePrivateKey, DecodePublicKey, EncodePrivateKey, EncodePublicKey},
};
use pkcs8::LineEnding;
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use zeroize::{Zeroize, Zeroizing};

use crate::{AgentClientError, AgentClientResult};

const LEGACY_SERVICE: &str = "inari-tray";
const LEGACY_ACCOUNT: &str = "local_trust_private_key";

#[derive(Clone)]
pub struct ClientIdentity {
    pub client_id: String,
    pub client_name: String,
    private_key_pem: SecretString,
    public_key_pem: String,
}

impl ClientIdentity {
    pub fn private_key_pem(&self) -> &SecretString {
        &self.private_key_pem
    }

    pub fn public_key_pem(&self) -> &str {
        &self.public_key_pem
    }
}

pub trait IdentityStore: Send + Sync {
    fn load(&self) -> AgentClientResult<Option<ClientIdentity>>;

    fn store(&self, identity: &ClientIdentity) -> AgentClientResult<()>;
}

#[derive(Clone, Copy, Debug, Default)]
pub struct LocalIdentityStore;

impl IdentityStore for LocalIdentityStore {
    fn load(&self) -> AgentClientResult<Option<ClientIdentity>> {
        let entry = keyring::Entry::new(LEGACY_SERVICE, LEGACY_ACCOUNT)
            .map_err(AgentClientError::IdentityUnavailable)?;
        let credential = match entry.get_password() {
            Ok(value) => Zeroizing::new(value),
            Err(keyring::Error::NoEntry) => return Ok(None),
            Err(error) => return Err(AgentClientError::IdentityUnavailable(error)),
        };
        let stored: StoredIdentity =
            serde_json::from_str(&credential).map_err(|_| AgentClientError::MalformedIdentity)?;
        stored.validate().map(Some)
    }

    fn store(&self, identity: &ClientIdentity) -> AgentClientResult<()> {
        let stored = StoredIdentity {
            client_id: identity.client_id.clone(),
            client_name: identity.client_name.clone(),
            private_key_pem: identity
                .private_key_pem
                .expose_secret()
                .to_owned(),
            public_key_pem: identity.public_key_pem.clone(),
        };
        let encoded = serde_json::to_string(&stored).map_err(AgentClientError::invalid_response)?;
        let entry = keyring::Entry::new(LEGACY_SERVICE, LEGACY_ACCOUNT)
            .map_err(AgentClientError::IdentityUnavailable)?;
        entry
            .set_password(&encoded)
            .map_err(AgentClientError::IdentityUnavailable)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredIdentity {
    client_id: String,
    client_name: String,
    private_key_pem: String,
    public_key_pem: String,
}

impl StoredIdentity {
    fn validate(self) -> AgentClientResult<ClientIdentity> {
        let private_key_pem = SecretString::from(self.private_key_pem);
        if self.client_id.trim().is_empty()
            || self.client_name.trim().is_empty()
            || self.public_key_pem.trim().is_empty()
        {
            return Err(AgentClientError::MalformedIdentity);
        }
        let signing_key = SigningKey::from_pkcs8_pem(private_key_pem.expose_secret())
            .map_err(|_| AgentClientError::MalformedIdentity)?;
        let verifying_key = VerifyingKey::from_public_key_pem(&self.public_key_pem)
            .map_err(|_| AgentClientError::MalformedIdentity)?;
        if signing_key.verifying_key() != verifying_key {
            return Err(AgentClientError::MalformedIdentity);
        }
        Ok(ClientIdentity {
            client_id: self.client_id,
            client_name: self.client_name,
            private_key_pem,
            public_key_pem: self.public_key_pem,
        })
    }
}

pub(crate) fn create_identity() -> AgentClientResult<ClientIdentity> {
    let mut seed = [0_u8; 32];
    getrandom::fill(&mut seed).map_err(AgentClientError::invalid_response)?;
    let signing_key = SigningKey::from_bytes(&seed);
    seed.zeroize();

    let private_key_pem = signing_key
        .to_pkcs8_pem(LineEnding::LF)
        .map_err(AgentClientError::invalid_response)?;
    let public_key_pem = signing_key
        .verifying_key()
        .to_public_key_pem(LineEnding::LF)
        .map_err(AgentClientError::invalid_response)?;
    let public_key_der = signing_key
        .verifying_key()
        .to_public_key_der()
        .map_err(AgentClientError::invalid_response)?;
    let fingerprint = hex::encode(Sha256::digest(public_key_der.as_bytes()));

    Ok(ClientIdentity {
        client_id: format!("device_center_{}", &fingerprint[..24]),
        client_name: "Inari Device Center".into(),
        private_key_pem: SecretString::from(private_key_pem.to_string()),
        public_key_pem,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_identity_round_trips_through_the_legacy_shape() {
        let identity = create_identity().expect("identity generation succeeds");
        let stored = StoredIdentity {
            client_id: identity.client_id.clone(),
            client_name: identity.client_name.clone(),
            private_key_pem: identity
                .private_key_pem
                .expose_secret()
                .to_owned(),
            public_key_pem: identity.public_key_pem.clone(),
        };
        let encoded = serde_json::to_string(&stored).expect("identity serializes");
        let decoded: StoredIdentity = serde_json::from_str(&encoded).expect("identity parses");

        assert_eq!(
            decoded
                .validate()
                .expect("identity validates")
                .client_id,
            identity.client_id
        );
    }

    #[test]
    fn rejects_a_public_key_that_does_not_match_the_private_key() {
        let identity = create_identity().expect("identity generation succeeds");
        let other = create_identity().expect("second identity generation succeeds");
        let stored = StoredIdentity {
            client_id: identity.client_id,
            client_name: identity.client_name,
            private_key_pem: identity
                .private_key_pem
                .expose_secret()
                .to_owned(),
            public_key_pem: other.public_key_pem,
        };

        assert!(matches!(stored.validate(), Err(AgentClientError::MalformedIdentity)));
    }
}
