use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use chrono::{DateTime, SubsecRound, TimeDelta, Utc};
use ed25519_dalek::SigningKey;
use ed25519_dalek::pkcs8::DecodePrivateKey;
use futures_util::future::{BoxFuture, try_join_all};
use inari_gateway::{GatewayError, GatewayRepository, NewRouterPolicy, RouterAdmission};
use inari_router::supervisor::RouterStatus;
use inari_router::{AgentAdmission, Policy, SignedPolicy};
use reqwest::{Certificate, Client, Identity, redirect};
use sha2::{Digest, Sha256};
use tokio::sync::{Mutex, watch};
use zeroize::Zeroizing;

use crate::config::{RouterManagementConfig, RouterPolicyConfig};
use crate::error::{AppError, AppResult};
use crate::shutdown::ShutdownCoordinator;
use crate::state::RouterPolicyReadiness;

const POLICY_EXPIRY: TimeDelta = TimeDelta::seconds(45);
const CLOCK_SKEW: TimeDelta = TimeDelta::seconds(5);
const REFRESH_MARGIN: TimeDelta = TimeDelta::seconds(15);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_RESPONSE_BYTES: usize = 64 * 1024;

#[cfg(test)]
#[path = "router_policy_tls.rs"]
mod tls_tests;

pub trait RouterManagement: Send + Sync {
    fn apply<'a>(
        &'a self,
        router: &'a RouterManagementConfig,
        policy: &'a SignedPolicy,
    ) -> BoxFuture<'a, AppResult<RouterStatus>>;
}

/// Reloads changed management credentials before a request and discards the old pool.
struct HttpsManagement {
    ca_file: PathBuf,
    certificate_file: PathBuf,
    private_key_file: PathBuf,
    current: Mutex<Option<ManagementClient>>,
}

struct ManagementClient {
    material: [u8; 32],
    client: Client,
}

impl RouterManagement for HttpsManagement {
    fn apply<'a>(
        &'a self,
        router: &'a RouterManagementConfig,
        policy: &'a SignedPolicy,
    ) -> BoxFuture<'a, AppResult<RouterStatus>> {
        Box::pin(async move {
            let address = router
                .address
                .join("policy")
                .map_err(|source| unavailable().with_source(source))?;
            let mut response = self
                .client()
                .await?
                .put(address)
                .json(policy)
                .send()
                .await
                .map_err(|source| unavailable().with_source(source))?
                .error_for_status()
                .map_err(|source| unavailable().with_source(source))?;
            let mut bytes = Vec::new();
            while let Some(chunk) = response
                .chunk()
                .await
                .map_err(|source| unavailable().with_source(source))?
            {
                if bytes.len().saturating_add(chunk.len()) > MAX_RESPONSE_BYTES {
                    return Err(unavailable());
                }
                bytes.extend_from_slice(&chunk);
            }
            serde_json::from_slice(&bytes).map_err(|source| unavailable().with_source(source))
        })
    }
}

impl HttpsManagement {
    async fn load(config: &RouterPolicyConfig) -> AppResult<Self> {
        let management = Self {
            ca_file: config.management_ca_file.clone(),
            certificate_file: config
                .management_certificate_file
                .clone(),
            private_key_file: config
                .management_private_key_file
                .clone(),
            current: Mutex::new(None),
        };
        management.client().await?;
        Ok(management)
    }

    async fn client(&self) -> AppResult<Client> {
        let mut current = self.current.lock().await;
        let client = self.refresh(&mut current).await;
        if client.is_err() {
            *current = None;
        }
        client
    }

    async fn refresh(&self, current: &mut Option<ManagementClient>) -> AppResult<Client> {
        let roots = read_material(&self.ca_file).await?;
        let mut identity = read_material(&self.certificate_file).await?;
        identity.extend_from_slice(&read_material(&self.private_key_file).await?);
        let material: [u8; 32] = Sha256::new()
            .chain_update(Sha256::digest(&*roots))
            .chain_update(&*identity)
            .finalize()
            .into();
        if let Some(current) = current
            .as_ref()
            .filter(|current| current.material == material)
        {
            return Ok(current.client.clone());
        }
        let certificates = Certificate::from_pem_bundle(&roots)
            .map_err(|source| unavailable().with_source(source))?;
        if certificates.is_empty() {
            return Err(unavailable());
        }
        let client = Client::builder()
            .https_only(true)
            .tls_certs_only(certificates)
            .identity(
                Identity::from_pem(&identity)
                    .map_err(|source| unavailable().with_source(source))?,
            )
            .redirect(redirect::Policy::none())
            .no_proxy()
            .connect_timeout(CONNECT_TIMEOUT)
            .timeout(REQUEST_TIMEOUT)
            .pool_max_idle_per_host(1)
            .build()
            .map_err(|source| unavailable().with_source(source))?;
        *current = Some(ManagementClient { material, client: client.clone() });
        Ok(client)
    }
}

async fn read_material(path: &Path) -> AppResult<Zeroizing<Vec<u8>>> {
    tokio::fs::read(path)
        .await
        .map(Zeroizing::new)
        .map_err(|source| unavailable().with_source(source))
}

#[derive(Clone, Copy)]
enum RouterPolicyHealth {
    Starting,
    ReadyUntil(DateTime<Utc>),
    Unavailable,
}

pub struct RouterAdmissionController {
    repository: GatewayRepository,
    organization_id: String,
    config: RouterPolicyConfig,
    namespace_prefix: String,
    signing_key: SigningKey,
    configuration_digest: [u8; 32],
    management: Arc<dyn RouterManagement>,
    reconciliation: Mutex<()>,
    health: watch::Sender<RouterPolicyHealth>,
}

impl RouterAdmissionController {
    pub async fn load(
        repository: GatewayRepository,
        organization_id: String,
        config: RouterPolicyConfig,
        namespace_prefix: String,
    ) -> AppResult<Self> {
        let pem = Zeroizing::new(
            tokio::fs::read_to_string(&config.signing_key_file)
                .await
                .map_err(|source| unavailable().with_source(source))?,
        );
        let signing_key =
            SigningKey::from_pkcs8_pem(&pem).map_err(|source| unavailable().with_source(source))?;
        let management = HttpsManagement::load(&config).await?;
        Self::new(
            repository,
            organization_id,
            config,
            namespace_prefix,
            signing_key,
            Arc::new(management),
        )
    }

    pub fn new(
        repository: GatewayRepository,
        organization_id: String,
        config: RouterPolicyConfig,
        namespace_prefix: String,
        signing_key: SigningKey,
        management: Arc<dyn RouterManagement>,
    ) -> AppResult<Self> {
        config.validate()?;
        let sample = router_policy(&config, &namespace_prefix, 1, vec![], Utc::now());
        SignedPolicy::sign(sample, &signing_key)
            .map_err(|source| unavailable().with_source(source))?;
        let configuration_digest = Sha256::digest(serde_json_canonicalizer::to_vec(&(
            &config.fleet_id,
            &config.routers,
            &config.trusted_peer_common_names,
            &namespace_prefix,
            signing_key.verifying_key().as_bytes(),
        ))?)
        .into();
        Ok(Self {
            repository,
            organization_id,
            config,
            namespace_prefix,
            signing_key,
            configuration_digest,
            management,
            reconciliation: Mutex::new(()),
            health: watch::channel(RouterPolicyHealth::Starting).0,
        })
    }

    pub async fn admit(&self, agent_id: &str) -> AppResult<RouterAdmission> {
        let admission = self.reconcile().await?;
        self.repository
            .require_router_admission(&admission, agent_id)
            .await?;
        Ok(admission)
    }

    pub(super) fn readiness(&self) -> RouterPolicyReadiness {
        match *self.health.borrow() {
            RouterPolicyHealth::Starting => {
                RouterPolicyReadiness::starting("Router policy awaits acknowledgment.")
            },
            RouterPolicyHealth::ReadyUntil(until) if until > Utc::now() => {
                RouterPolicyReadiness::ready("Every Router acknowledged the current policy.")
            },
            RouterPolicyHealth::ReadyUntil(_) | RouterPolicyHealth::Unavailable => {
                RouterPolicyReadiness::degraded(
                    "Router acknowledgment is unavailable or expired. Managed admission is closed.",
                )
            },
        }
    }

    pub(super) async fn reconcile(&self) -> AppResult<RouterAdmission> {
        let _guard = self.reconciliation.lock().await;
        let result = self.apply_current_policy().await;
        self.health.send_replace(match &result {
            Ok(admission) => RouterPolicyHealth::ReadyUntil(admission.valid_until()),
            Err(_) => RouterPolicyHealth::Unavailable,
        });
        result
    }

    async fn apply_current_policy(&self) -> AppResult<RouterAdmission> {
        let policy = self
            .repository
            .prepare_router_policy(
                &self.organization_id,
                &self.configuration_digest,
                Utc::now() + REFRESH_MARGIN,
                |generation, agents| {
                    let agents = agents
                        .iter()
                        .map(|agent| AgentAdmission {
                            common_name: agent.agent_id.clone(),
                            namespace: agent.namespace.clone(),
                        })
                        .collect();
                    let signed = SignedPolicy::sign(
                        router_policy(
                            &self.config,
                            &self.namespace_prefix,
                            generation,
                            agents,
                            Utc::now(),
                        ),
                        &self.signing_key,
                    )
                    .map_err(|error| GatewayError::Unavailable(error.to_string()))?;
                    let verified = signed
                        .verify(&self.signing_key.verifying_key(), &self.config.fleet_id)
                        .map_err(|error| GatewayError::Unavailable(error.to_string()))?;
                    let digest = hex::decode(verified.digest())
                        .map_err(|error| GatewayError::Unavailable(error.to_string()))?
                        .try_into()
                        .map_err(|_| {
                            GatewayError::CorruptState("Router digest is invalid".into())
                        })?;
                    Ok(NewRouterPolicy {
                        digest,
                        expires_at: signed.policy.expires_at,
                        signed_policy: serde_json::to_value(signed)?,
                    })
                },
            )
            .await?;
        let signed: SignedPolicy = serde_json::from_value(policy.signed_policy.clone())?;
        let verified = signed
            .verify(&self.signing_key.verifying_key(), &self.config.fleet_id)
            .map_err(|source| unavailable().with_source(source))?;
        verified
            .require_current(Utc::now())
            .map_err(|source| unavailable().with_source(source))?;
        if hex::encode(policy.digest) != verified.digest()
            || signed.policy.generation != policy.generation
        {
            return Err(unavailable());
        }
        let statuses = tokio::time::timeout(
            REQUEST_TIMEOUT,
            try_join_all(
                self.config
                    .routers
                    .iter()
                    .map(|router| self.management.apply(router, &signed)),
            ),
        )
        .await
        .map_err(|source| unavailable().with_source(source))??;
        if statuses.len() != self.config.routers.len()
            || statuses.is_empty()
            || statuses.iter().any(|status| {
                !status.is_current()
                    || status.generation != Some(policy.generation)
                    || status.digest.as_deref() != Some(verified.digest())
                    || status.expires_at != Some(policy.expires_at)
            })
        {
            return Err(unavailable());
        }
        Ok(self
            .repository
            .acknowledge_router_policy(&self.organization_id, &policy)
            .await?)
    }

    pub async fn run(&self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        let mut ticks = tokio::time::interval(Duration::from_secs(5));
        ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                _ = shutdown.wait_for_shutdown() => return Ok(()),
                _ = ticks.tick() => {
                    if let Err(error) = self.reconcile().await {
                        tracing::warn!(error = %error, "Router policy reconciliation failed; admission remains closed");
                    }
                }
            }
        }
    }
}

/// Backdates activation for clock skew without extending the dispatch deadline.
fn router_policy(
    config: &RouterPolicyConfig,
    namespace_prefix: &str,
    generation: u64,
    agents: Vec<AgentAdmission>,
    now: DateTime<Utc>,
) -> Policy {
    let now = now.trunc_subsecs(6);
    Policy {
        version: 1,
        fleet_id: config.fleet_id.clone(),
        generation,
        issued_at: now - CLOCK_SKEW,
        not_before: now - CLOCK_SKEW,
        expires_at: now + POLICY_EXPIRY,
        namespace_prefix: namespace_prefix.into(),
        trusted_peer_common_names: config.trusted_peer_common_names.clone(),
        agents,
    }
}

fn unavailable() -> AppError {
    AppError::service_unavailable(
        "Every Router must acknowledge the current Agent policy before managed admission.",
    )
}
