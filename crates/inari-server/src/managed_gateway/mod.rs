use std::sync::Arc;

use inari_gateway::GatewayRepository;
use inari_gateway::certificate::CertificateIssuerHandle;

use crate::config::{ManagedGatewayConfig, OrganizationConfig, ZenohConfig};
use crate::error::{AppError, AppResult};
use crate::zenoh::ZenohHandle;

mod certificate;
mod dispatch;
mod enrollment;
mod fleet;
mod jobs;
mod keyspace;
mod managed_work;
mod models;
mod runtime;
mod store;

pub use self::certificate::StepCaIssuer;
pub use self::dispatch::ManagedDispatchSigner;

pub use self::models::{AgentPublicationList, CommandHistory, JobList, JobReceipt, JobRequest};
use self::models::{StoredAgentEnrollment, StoredControllerCommand};
use self::store::ManagedGatewayStore;

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
    dispatch_signer: Option<Arc<ManagedDispatchSigner>>,
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
        dispatch_signer: Option<Arc<ManagedDispatchSigner>>,
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
                dispatch_signer,
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
            .dispatch_signer
            .as_deref()
            .ok_or_else(|| AppError::service_unavailable("Managed Work dispatch is not enabled."))
    }
}
