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
mod jobs;
mod keyspace;
mod managed_work;
mod models;
mod payload;
mod runtime;
mod store;

pub use self::certificate::StepCaIssuer;
pub use self::dispatch::ManagedDispatchSigner;
pub use self::payload::{ManagedPayloadProtector, OpenBaoTransitKeyWrapper};

use self::models::StoredControllerCommand;
pub use self::models::{AgentPublicationList, CommandHistory, JobList, JobReceipt, JobRequest};
use self::store::ManagedGatewayStore;

pub struct ManagedWorkSecurity {
    dispatch_signer: ManagedDispatchSigner,
    payload_protector: ManagedPayloadProtector,
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
}

impl ManagedGatewayController {
    #[must_use]
    pub fn new(
        config: ManagedGatewayConfig,
        organization: OrganizationConfig,
        zenoh_config: ZenohConfig,
        zenoh: ZenohHandle,
        repository: Option<GatewayRepository>,
        certificate_issuer: Option<CertificateIssuerHandle>,
        security: Option<Arc<ManagedWorkSecurity>>,
    ) -> Self {
        let store = ManagedGatewayStore::new(repository);
        Self {
            inner: Arc::new(ManagedGatewayControllerInner {
                config,
                zenoh_config,
                zenoh,
                store,
                organization,
                certificate_issuer,
                security,
            }),
        }
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
