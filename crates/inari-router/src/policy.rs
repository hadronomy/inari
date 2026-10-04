use std::collections::BTreeSet;

use chrono::{DateTime, Duration, Utc};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::{RouterError, RouterResult};

const SIGNATURE_DOMAIN: &[u8] = b"inari.router-policy.v1\n";
const MAX_AGENTS: usize = 10_000;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentAdmission {
    pub common_name: String,
    pub namespace: String,
}

/// The complete authority for one Router fleet, with a lifetime of at most five minutes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub version: u8,
    pub fleet_id: String,
    pub generation: u64,
    pub issued_at: DateTime<Utc>,
    pub not_before: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub namespace_prefix: String,
    pub trusted_peer_common_names: Vec<String>,
    pub agents: Vec<AgentAdmission>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignedPolicy {
    pub policy: Policy,
    pub signature: String,
}

/// A policy whose signature, identity scope, and static limits passed validation.
///
/// Call [`Self::require_current`] before activation. Expired policies retain their
/// generation so that a restart cannot admit an older signed policy.
#[derive(Debug, Clone)]
pub struct VerifiedPolicy {
    signed: SignedPolicy,
    digest: String,
}

impl SignedPolicy {
    pub fn sign(policy: Policy, key: &SigningKey) -> RouterResult<Self> {
        policy.validate()?;
        let signature = hex::encode(
            key.sign(&signature_input(&policy)?)
                .to_bytes(),
        );
        Ok(Self { policy, signature })
    }

    pub fn verify(&self, key: &VerifyingKey, fleet_id: &str) -> RouterResult<VerifiedPolicy> {
        self.policy.validate()?;
        if key.is_weak() || self.policy.fleet_id != fleet_id {
            return Err(RouterError::InvalidSignature);
        }
        let raw = hex::decode(&self.signature).map_err(|_| RouterError::InvalidSignature)?;
        let signature = Signature::from_slice(&raw).map_err(|_| RouterError::InvalidSignature)?;
        key.verify_strict(&signature_input(&self.policy)?, &signature)
            .map_err(|_| RouterError::InvalidSignature)?;
        let digest = hex::encode(Sha256::digest(canonical(&self.policy)?));
        Ok(VerifiedPolicy { signed: self.clone(), digest })
    }
}

impl VerifiedPolicy {
    #[must_use]
    pub fn policy(&self) -> &Policy {
        &self.signed.policy
    }

    #[must_use]
    pub fn signed(&self) -> &SignedPolicy {
        &self.signed
    }

    #[must_use]
    pub fn digest(&self) -> &str {
        &self.digest
    }

    pub(crate) fn has_same_authority(&self, other: &Self) -> bool {
        let policy = self.policy();
        let other = other.policy();
        policy.version == other.version
            && policy.fleet_id == other.fleet_id
            && policy.namespace_prefix == other.namespace_prefix
            && policy.trusted_peer_common_names == other.trusted_peer_common_names
            && policy.agents == other.agents
    }

    pub fn require_current(&self, now: DateTime<Utc>) -> RouterResult<()> {
        if self.policy().not_before > now || self.policy().expires_at <= now {
            return Err(RouterError::NotCurrent);
        }
        Ok(())
    }

    /// Returns whether this policy advances the durable generation.
    pub fn advances(&self, previous: &Self) -> RouterResult<bool> {
        match self
            .policy()
            .generation
            .cmp(&previous.policy().generation)
        {
            std::cmp::Ordering::Greater => Ok(true),
            std::cmp::Ordering::Equal if self.digest == previous.digest => Ok(false),
            _ => Err(RouterError::GenerationConflict),
        }
    }
}

impl Policy {
    fn validate(&self) -> RouterResult<()> {
        if self.version != 1
            || !(1..=9_007_199_254_740_991).contains(&self.generation)
            || !literal_name(&self.fleet_id)
        {
            return invalid("version, fleet_id, or generation is invalid");
        }
        let lifetime = self
            .expires_at
            .signed_duration_since(self.issued_at);
        if self.issued_at > self.not_before
            || self.not_before >= self.expires_at
            || lifetime <= Duration::zero()
            || lifetime > Duration::minutes(5)
        {
            return invalid("policy times must fit within five minutes");
        }
        if !literal_namespace(&self.namespace_prefix) {
            return invalid("namespace_prefix must contain only literal path segments");
        }
        if self
            .trusted_peer_common_names
            .is_empty()
            || self.trusted_peer_common_names.len() > 64
            || self.agents.len() > MAX_AGENTS
        {
            return invalid("the Agent or trusted peer count is invalid");
        }
        let mut subjects = BTreeSet::new();
        for peer in &self.trusted_peer_common_names {
            if !literal_name(peer) || peer.starts_with("agt_") || !subjects.insert(peer) {
                return invalid("trusted peer common names must be distinct literal names");
            }
        }
        for agent in &self.agents {
            if !agent_name(&agent.common_name)
                || !subjects.insert(&agent.common_name)
                || agent.namespace != format!("{}/{}", self.namespace_prefix, agent.common_name)
            {
                return invalid("each Agent requires its own common name and exact namespace");
            }
        }
        Ok(())
    }
}

fn agent_name(value: &str) -> bool {
    value
        .strip_prefix("agt_")
        .is_some_and(|suffix| {
            suffix.len() == 24
                && suffix
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
}

pub(crate) fn literal_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn literal_namespace(value: &str) -> bool {
    value.len() <= 256
        && value.split('/').all(|segment| {
            !segment.is_empty() && segment != "." && segment != ".." && literal_name(segment)
        })
}

fn signature_input(policy: &Policy) -> RouterResult<Vec<u8>> {
    let mut bytes = SIGNATURE_DOMAIN.to_vec();
    bytes.extend(canonical(policy)?);
    Ok(bytes)
}

fn canonical(policy: &Policy) -> RouterResult<Vec<u8>> {
    serde_json_canonicalizer::to_vec(policy).map_err(RouterError::from)
}

fn invalid<T>(message: &str) -> RouterResult<T> {
    Err(RouterError::InvalidPolicy(message.into()))
}
