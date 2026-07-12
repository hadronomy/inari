use std::sync::Arc;

use crate::GatewayResult;
use crate::protocol::{AgentId, CertificateBootstrapAuth};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CertificateRequest {
    pub agent_id: AgentId,
    pub authorized_sans: Vec<String>,
    pub csr_fingerprint: String,
}

pub trait CertificateIssuer: Send + Sync {
    fn issue(&self, request: &CertificateRequest) -> GatewayResult<CertificateBootstrapAuth>;
}

pub type DynCertificateIssuer = dyn CertificateIssuer + Send + Sync + 'static;
pub type CertificateIssuerHandle = Arc<DynCertificateIssuer>;
