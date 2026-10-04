use std::future::Future;
use std::io::{self, BufReader};
use std::net::SocketAddr;
use std::path::Path;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::{DefaultBodyLimit, State};
use axum::http::StatusCode;
use axum::middleware::AddExtension;
use axum::routing::{get, put};
use axum::{Extension, Json, Router};
use axum_server::accept::Accept;
use axum_server::tls_rustls::{RustlsAcceptor, RustlsConfig};
use chrono::{DateTime, Utc};
use rustls::server::WebPkiClientVerifier;
use rustls::{RootCertStore, ServerConfig};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::sync::Mutex;
use tokio_rustls::server::TlsStream;
use tower::Layer;
use tower_http::timeout::TimeoutLayer;
use x509_parser::prelude::FromDer;

use crate::policy::literal_name;
use crate::supervisor::{RouterStatus, SupervisorHandle};
use crate::tls::{TlsFiles, TlsMaterial};
use crate::{RouterError, RouterResult, SignedPolicy};

/// The management CA authorizes a dedicated Controller identity, separate from Agent TLS.
#[derive(Debug, Clone)]
pub struct ManagementAcceptor {
    files: Arc<TlsFiles>,
    current: Arc<Mutex<CachedAcceptor>>,
    common_name: Arc<str>,
}

#[derive(Debug)]
struct CachedAcceptor {
    digest: [u8; 32],
    inner: RustlsAcceptor,
}

#[derive(Debug, Clone)]
pub struct ManagementPeer {
    not_before: DateTime<Utc>,
    expires_at: DateTime<Utc>,
}

impl ManagementAcceptor {
    pub async fn from_files(
        certificate: &Path,
        private_key: &Path,
        client_ca: &Path,
        common_name: &str,
    ) -> RouterResult<Self> {
        if !literal_name(common_name) || common_name.starts_with("agt_") {
            return Err(RouterError::InvalidPolicy(
                "management requires a dedicated Controller common name".into(),
            ));
        }
        let files = TlsFiles {
            root_ca: client_ca.to_owned(),
            certificate: certificate.to_owned(),
            private_key: private_key.to_owned(),
        };
        let material = TlsMaterial::load(&files).await?;
        let current = Self::acceptor(&material)?;
        Ok(Self {
            files: Arc::new(files),
            current: Arc::new(Mutex::new(current)),
            common_name: common_name.into(),
        })
    }

    fn acceptor(material: &TlsMaterial) -> RouterResult<CachedAcceptor> {
        let roots = rustls_pemfile::certs(&mut BufReader::new(material.root_ca.as_slice()))
            .collect::<Result<Vec<_>, _>>()?;
        if roots.is_empty() {
            return Err(RouterError::InvalidPolicy("management client CA is empty".into()));
        }
        let mut root_store = RootCertStore::empty();
        for root in roots {
            root_store
                .add(root)
                .map_err(io::Error::other)?;
        }
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let verifier =
            WebPkiClientVerifier::builder_with_provider(Arc::new(root_store), provider.clone())
                .build()
                .map_err(io::Error::other)?;
        let chain = rustls_pemfile::certs(&mut BufReader::new(material.certificate.as_slice()))
            .collect::<Result<Vec<_>, _>>()?;
        let key =
            rustls_pemfile::private_key(&mut BufReader::new(material.private_key.as_slice()))?
                .ok_or_else(|| io::Error::other("management private key is absent"))?;
        let mut config = ServerConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map_err(io::Error::other)?
            .with_client_cert_verifier(verifier)
            .with_single_cert(chain, key)
            .map_err(io::Error::other)?;
        config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
        Ok(CachedAcceptor {
            digest: material.digest,
            inner: RustlsAcceptor::new(RustlsConfig::from_config(Arc::new(config)))
                .handshake_timeout(Duration::from_secs(5)),
        })
    }

    async fn current_acceptor(&self) -> io::Result<RustlsAcceptor> {
        let mut current = self.current.lock().await;
        let material = TlsMaterial::load(&self.files)
            .await
            .map_err(io::Error::other)?;
        if current.digest != material.digest {
            *current = Self::acceptor(&material).map_err(io::Error::other)?;
        }
        Ok(current.inner.clone())
    }
}

impl<I, S> Accept<I, S> for ManagementAcceptor
where
    I: AsyncRead + AsyncWrite + Unpin + Send + 'static,
    S: Send + 'static,
{
    type Stream = TlsStream<I>;
    type Service = AddExtension<S, ManagementPeer>;
    type Future = Pin<Box<dyn Future<Output = io::Result<(Self::Stream, Self::Service)>> + Send>>;

    fn accept(&self, stream: I, service: S) -> Self::Future {
        let management = self.clone();
        let common_name = self.common_name.clone();
        Box::pin(async move {
            let (stream, service) = tokio::time::timeout(Duration::from_secs(5), async {
                let acceptor = management.current_acceptor().await?;
                acceptor.accept(stream, service).await
            })
            .await
            .map_err(io::Error::other)??;
            let peer = stream
                .get_ref()
                .1
                .peer_certificates()
                .and_then(|chain| chain.first())
                .ok_or_else(|| io::Error::other("management client certificate is absent"))?;
            let identity = peer_identity(peer.as_ref(), &common_name)?;
            Ok((stream, Extension(identity).layer(service)))
        })
    }
}

fn peer_identity(der: &[u8], expected: &str) -> io::Result<ManagementPeer> {
    let (remaining, certificate) =
        x509_parser::certificate::X509Certificate::from_der(der).map_err(io::Error::other)?;
    let mut common_names = certificate.subject().iter_common_name();
    let name = common_names
        .next()
        .and_then(|name| name.as_str().ok());
    if !remaining.is_empty() || name != Some(expected) || common_names.next().is_some() {
        return Err(io::Error::other("management client identity is not authorized"));
    }
    let expires_at = DateTime::from_timestamp(
        certificate
            .validity()
            .not_after
            .timestamp(),
        0,
    )
    .ok_or_else(|| io::Error::other("management certificate expiry is invalid"))?;
    let not_before = DateTime::from_timestamp(
        certificate
            .validity()
            .not_before
            .timestamp(),
        0,
    )
    .ok_or_else(|| io::Error::other("management certificate start time is invalid"))?;
    Ok(ManagementPeer { not_before, expires_at })
}

pub fn routes(handle: SupervisorHandle) -> Router {
    Router::new()
        .route("/policy", put(apply_policy))
        .route("/status", get(status))
        .route("/readyz", get(ready))
        .layer(DefaultBodyLimit::max(2 * 1024 * 1024))
        .layer(TimeoutLayer::with_status_code(StatusCode::REQUEST_TIMEOUT, Duration::from_secs(30)))
        .with_state(handle)
}

pub async fn serve(
    address: SocketAddr,
    acceptor: ManagementAcceptor,
    handle: SupervisorHandle,
    shutdown: axum_server::Handle<SocketAddr>,
) -> io::Result<()> {
    let mut server = axum_server::bind(address)
        .acceptor(acceptor)
        .handle(shutdown);
    server
        .http_builder()
        .http1()
        .header_read_timeout(Duration::from_secs(5));
    server
        .serve(routes(handle).into_make_service())
        .await
}

async fn apply_policy(
    State(handle): State<SupervisorHandle>,
    Extension(peer): Extension<ManagementPeer>,
    Json(policy): Json<SignedPolicy>,
) -> Result<Json<RouterStatus>, (StatusCode, String)> {
    authorize(&peer)?;
    let status = handle
        .apply(policy)
        .await
        .map_err(response_error)?;
    authorize(&peer)?;
    Ok(Json(status))
}

async fn status(
    State(handle): State<SupervisorHandle>,
    Extension(peer): Extension<ManagementPeer>,
) -> Result<Json<RouterStatus>, (StatusCode, String)> {
    authorize(&peer)?;
    Ok(Json(handle.status()))
}

async fn ready(
    State(handle): State<SupervisorHandle>,
    Extension(peer): Extension<ManagementPeer>,
) -> Result<(StatusCode, Json<RouterStatus>), (StatusCode, String)> {
    authorize(&peer)?;
    let status = handle.status();
    let code = if status.ready { StatusCode::OK } else { StatusCode::SERVICE_UNAVAILABLE };
    Ok((code, Json(status)))
}

fn authorize(peer: &ManagementPeer) -> Result<(), (StatusCode, String)> {
    if peer.not_before > Utc::now() || peer.expires_at <= Utc::now() {
        Err((StatusCode::FORBIDDEN, "Management client certificate is not current.".into()))
    } else {
        Ok(())
    }
}

fn response_error(error: RouterError) -> (StatusCode, String) {
    let status = match error {
        RouterError::GenerationConflict => StatusCode::CONFLICT,
        RouterError::InvalidPolicy(_) | RouterError::InvalidSignature | RouterError::NotCurrent => {
            StatusCode::BAD_REQUEST
        },
        _ => StatusCode::SERVICE_UNAVAILABLE,
    };
    (status, error.to_string())
}
