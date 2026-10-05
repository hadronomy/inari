use std::sync::Arc;

use inari_gateway::GatewayRepository;
use inari_gateway::certificate::CertificateIssuerHandle;

use crate::config::{ManagedGatewayConfig, OrganizationConfig, ZenohConfig};
use crate::error::{AppError, AppResult};
use crate::zenoh::ZenohHandle;

mod certificate;
mod dispatch;
mod enrollment;
#[cfg(test)]
mod enrollment_tests;
mod fleet;
#[cfg(test)]
mod inventory_tests;
mod jobs;
mod keyspace;
mod managed_work;
mod models;
mod payload;
mod router_policy;
mod runtime;
mod store;

pub use self::certificate::StepCaIssuer;
pub use self::dispatch::ManagedDispatchSigner;
pub use self::payload::{ManagedPayloadProtector, OpenBaoTransitKeyWrapper};
pub use self::router_policy::{RouterAdmissionController, RouterManagement};

use self::models::StoredControllerCommand;
pub use self::models::{AgentPublicationList, CommandHistory, JobList, JobReceipt, JobRequest};
use self::store::ManagedGatewayStore;

pub struct ManagedWorkSecurity {
    dispatch_signer: ManagedDispatchSigner,
    payload_protector: ManagedPayloadProtector,
}

#[derive(Default)]
pub struct ManagedGatewaySecurity {
    pub certificate_issuer: Option<CertificateIssuerHandle>,
    pub managed_work: Option<Arc<ManagedWorkSecurity>>,
    pub router_admission: Option<Arc<RouterAdmissionController>>,
}

impl ManagedWorkSecurity {
    pub fn new(
        dispatch_signer: ManagedDispatchSigner,
        payload_protector: ManagedPayloadProtector,
    ) -> Self {
        Self { dispatch_signer, payload_protector }
    }
}

#[derive(Clone)]
pub struct ManagedGatewayController {
    inner: Arc<ManagedGatewayControllerInner>,
}

struct ManagedGatewayControllerInner {
    config: ManagedGatewayConfig,
    zenoh_config: ZenohConfig,
    zenoh: ZenohHandle,
    store: ManagedGatewayStore,
    organization: OrganizationConfig,
    certificate_issuer: Option<CertificateIssuerHandle>,
    security: Option<Arc<ManagedWorkSecurity>>,
    router_admission: Option<Arc<RouterAdmissionController>>,
}

impl ManagedGatewayController {
    #[must_use]
    pub fn new(
        config: ManagedGatewayConfig,
        organization: OrganizationConfig,
        zenoh_config: ZenohConfig,
        zenoh: ZenohHandle,
        repository: Option<GatewayRepository>,
        security: ManagedGatewaySecurity,
    ) -> Self {
        let store = ManagedGatewayStore::new(repository);
        Self {
            inner: Arc::new(ManagedGatewayControllerInner {
                config,
                zenoh_config,
                zenoh,
                store,
                organization,
                certificate_issuer: security.certificate_issuer,
                security: security.managed_work,
                router_admission: security.router_admission,
            }),
        }
    }

    pub(crate) fn router_policy_readiness(&self) -> crate::state::RouterPolicyReadiness {
        if !self.is_enabled() {
            return crate::state::RouterPolicyReadiness::disabled(
                "Managed Router policy is disabled.",
            );
        }
        self.inner
            .router_admission
            .as_ref()
            .map_or_else(
                || {
                    crate::state::RouterPolicyReadiness::degraded(
                        "Router admission is not configured.",
                    )
                },
                |admission| admission.readiness(),
            )
    }

    #[must_use]
    pub fn is_enabled(&self) -> bool {
        self.inner.config.enabled
    }

    fn ensure_enabled(&self) -> AppResult<()> {
        if self.inner.config.enabled && self.inner.store.is_available() {
            Ok(())
        } else {
            Err(AppError::service_unavailable("Managed gateway controller is not enabled."))
        }
    }

    fn dispatch_signer(&self) -> AppResult<&ManagedDispatchSigner> {
        self.inner
            .security
            .as_deref()
            .map(|security| &security.dispatch_signer)
            .ok_or_else(|| AppError::service_unavailable("Managed Work dispatch is not enabled."))
    }

    async fn router_admission(&self, agent_id: &str) -> AppResult<inari_gateway::RouterAdmission> {
        self.inner
            .router_admission
            .as_ref()
            .ok_or_else(|| AppError::service_unavailable("Router admission is not configured."))?
            .admit(agent_id)
            .await
    }

    fn payload_protector(&self) -> AppResult<&ManagedPayloadProtector> {
        self.inner
            .security
            .as_deref()
            .map(|security| &security.payload_protector)
            .ok_or_else(|| {
                AppError::service_unavailable("Managed Payload protection is not enabled.")
            })
    }
}

#[cfg(test)]
mod router_policy_tests;
