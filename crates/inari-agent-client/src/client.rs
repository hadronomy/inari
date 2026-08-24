use std::{sync::Arc, time::Duration};

use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use ed25519_dalek::{Signer as _, pkcs8::DecodePrivateKey};
use reqwest::header::{AUTHORIZATION, HeaderMap, HeaderValue};
use secrecy::{ExposeSecret, SecretString};
use tokio::sync::Mutex;
use url::Url;

use crate::{
    AgentClientError, AgentClientResult, AgentEventStream, Device, DeviceId, EnrollmentPreview,
    InvitationLink, Job, PairingMode, SetupSnapshot,
    identity::{ClientIdentity, IdentityStore, create_identity},
    pairing::PairingGrant,
    transport,
};

const DEFAULT_AGENT_ENDPOINT: &str = "http://127.0.0.1:7310/";

/// What the last read of the credential store produced.
enum IdentityState {
    Unread,
    Ready(ClientIdentity),
    Unavailable(String),
}

#[derive(Clone, Debug)]
pub struct AgentClientOptions {
    pub endpoint: Url,
    pub pairing_mode: PairingMode,
    pub request_timeout: Duration,
}

impl Default for AgentClientOptions {
    fn default() -> Self {
        Self {
            endpoint: Url::parse(DEFAULT_AGENT_ENDPOINT)
                .expect("the built-in local endpoint is valid"),
            pairing_mode: PairingMode::default(),
            request_timeout: Duration::from_secs(10),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_to_the_agent_listener() {
        assert_eq!(
            AgentClientOptions::default()
                .endpoint
                .as_str(),
            DEFAULT_AGENT_ENDPOINT
        );
    }

    #[test]
    fn generated_transport_base_has_no_trailing_slash() {
        let endpoint = AgentClientOptions::default().endpoint;

        assert_eq!(generated_transport_base(&endpoint), "http://127.0.0.1:7310");
    }

    #[tokio::test]
    async fn communication_errors_report_an_unavailable_agent() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        drop(listener);
        let error = reqwest::Client::new()
            .get(format!("http://{address}"))
            .send()
            .await
            .unwrap_err();

        let error = map_transport_error(progenitor_client::Error::<()>::CommunicationError(error));

        assert!(matches!(error, AgentClientError::Unavailable(_)));
    }
}

pub struct AgentClient {
    endpoint: Url,
    http: reqwest::Client,
    identity: Arc<dyn IdentityStore>,
    /// The outcome of reading [`Self::identity`], held for the life of the
    /// client.
    ///
    /// The store reads the OS credential vault, and on macOS a read the user
    /// has not permanently allowed raises a system prompt. Both outcomes are
    /// cached, not just the successful one: an agent that is not running keeps
    /// the reconnect loop turning, and a read that is failing on each pass is
    /// exactly the case that turns into a stream of prompts the operator
    /// cannot get rid of. A failure is cleared only by
    /// [`AgentClient::forget_identity`], so asking again is something the user
    /// does deliberately.
    resolved_identity: Mutex<IdentityState>,
    pairing_mode: PairingMode,
    request_timeout: Duration,
    token: Mutex<Option<AccessToken>>,
}

impl AgentClient {
    pub fn new(
        options: AgentClientOptions,
        identity: impl IdentityStore + 'static,
    ) -> AgentClientResult<Self> {
        let http = reqwest::Client::builder()
            .timeout(options.request_timeout)
            .build()
            .map_err(AgentClientError::Unavailable)?;
        Ok(Self {
            endpoint: options.endpoint,
            http,
            identity: Arc::new(identity),
            resolved_identity: Mutex::new(IdentityState::Unread),
            pairing_mode: options.pairing_mode,
            request_timeout: options.request_timeout,
            token: Mutex::new(None),
        })
    }

    pub fn has_identity(&self) -> AgentClientResult<bool> {
        self.identity
            .load()
            .map(|identity| identity.is_some())
    }

    /// This client's identity, reading the credential store at most once.
    ///
    /// A missing identity is created and stored on the first call. After that
    /// both success and failure are served from memory, so a long-running
    /// reconnect loop never touches the vault again.
    async fn identity(&self) -> AgentClientResult<ClientIdentity> {
        let mut cached = self.resolved_identity.lock().await;
        match &*cached {
            IdentityState::Ready(identity) => return Ok(identity.clone()),
            IdentityState::Unavailable(reason) => {
                return Err(AgentClientError::IdentityLocked(reason.clone()));
            },
            IdentityState::Unread => {},
        }

        let resolved: AgentClientResult<ClientIdentity> = async {
            match load_identity(self.identity.clone()).await? {
                Some(identity) => Ok(identity),
                None => {
                    let identity = create_identity()?;
                    store_identity(self.identity.clone(), identity.clone()).await?;
                    Ok(identity)
                },
            }
        }
        .await;

        match resolved {
            Ok(identity) => {
                *cached = IdentityState::Ready(identity.clone());
                Ok(identity)
            },
            Err(error) => {
                *cached = IdentityState::Unavailable(error.to_string());
                Err(error)
            },
        }
    }

    /// Drop the cached identity so the next request reads the credential store
    /// again. Call this from an explicit user retry, never from a timer.
    pub async fn forget_identity(&self) {
        *self.resolved_identity.lock().await = IdentityState::Unread;
        *self.token.lock().await = None;
    }

    pub async fn setup(&self) -> AgentClientResult<SetupSnapshot> {
        let transport = self.authorized_transport().await?;
        let response = transport
            .managed_onboarding_status()
            .await
            .map_err(map_transport_error)?;
        super::model::SetupSnapshot::try_from(response.into_inner())
    }

    pub async fn preview(
        &self,
        invitation: &InvitationLink,
    ) -> AgentClientResult<EnrollmentPreview> {
        let transport = self.authorized_transport().await?;
        let request = transport::types::ManagedOnboardingInvitationRequest {
            controller_url: None,
            invitation: invitation.transport_value()?,
        };
        let response = transport
            .preview_managed_onboarding(&request)
            .await
            .map_err(map_transport_error)?;
        EnrollmentPreview::try_from(response.into_inner())
    }

    pub async fn begin_setup(
        &self,
        invitation: &InvitationLink,
    ) -> AgentClientResult<SetupSnapshot> {
        let transport = self.authorized_transport().await?;
        let request = transport::types::ManagedOnboardingInvitationRequest {
            controller_url: None,
            invitation: invitation.transport_value()?,
        };
        transport
            .start_managed_onboarding(&request)
            .await
            .map_err(map_transport_error)?;
        self.setup().await
    }

    pub async fn confirm_devices(
        &self,
        device_ids: impl IntoIterator<Item = DeviceId>,
    ) -> AgentClientResult<SetupSnapshot> {
        let transport = self.authorized_transport().await?;
        let request = transport::types::ManagedOnboardingDeviceConfirmationRequest {
            default_printer_device_id: None,
            device_ids: device_ids
                .into_iter()
                .map(|id| id.to_string())
                .collect(),
            labels: std::collections::HashMap::new(),
        };
        let response = transport
            .confirm_onboarding_devices(&request)
            .await
            .map_err(map_transport_error)?;
        SetupSnapshot::try_from(response.into_inner())
    }

    pub async fn cancel_setup(&self) -> AgentClientResult<SetupSnapshot> {
        let transport = self.authorized_transport().await?;
        let response = transport
            .cancel_managed_onboarding()
            .await
            .map_err(map_transport_error)?;
        SetupSnapshot::try_from(response.into_inner())
    }

    pub async fn devices(&self) -> AgentClientResult<Vec<Device>> {
        let transport = self.authorized_transport().await?;
        let response = transport
            .list_devices()
            .await
            .map_err(map_transport_error)?;
        super::model::map_devices(response.into_inner())
    }

    pub async fn jobs(&self) -> AgentClientResult<Vec<Job>> {
        let transport = self.authorized_transport().await?;
        let response = transport
            .list_jobs(None, None)
            .await
            .map_err(map_transport_error)?;
        super::model::map_jobs(response.into_inner())
    }

    pub async fn events(&self) -> AgentClientResult<AgentEventStream> {
        let token = self.access_token().await?;
        AgentEventStream::connect(&self.endpoint, &token.access_token).await
    }

    async fn authorized_transport(&self) -> AgentClientResult<transport::Client> {
        let token = self.access_token().await?;
        let mut headers = HeaderMap::new();
        let mut authorization =
            HeaderValue::from_str(&format!("Bearer {}", token.access_token.expose_secret()))
                .map_err(AgentClientError::invalid_response)?;
        authorization.set_sensitive(true);
        headers.insert(AUTHORIZATION, authorization);
        let http = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(self.request_timeout)
            .build()
            .map_err(AgentClientError::Unavailable)?;
        Ok(transport::Client::new_with_client(generated_transport_base(&self.endpoint), http))
    }

    async fn access_token(&self) -> AgentClientResult<AccessToken> {
        let mut cached = self.token.lock().await;
        if let Some(token) = cached.as_ref()
            && token.expires_at > Utc::now() + ChronoDuration::seconds(30)
        {
            return Ok(token.clone());
        }

        let identity = self.identity().await?;
        let transport = transport::Client::new_with_client(
            generated_transport_base(&self.endpoint),
            self.http.clone(),
        );
        let request = self
            .access_token_request(&transport, &identity)
            .await?;
        let response = match transport
            .issue_local_token(&request)
            .await
        {
            Ok(response) => response.into_inner(),
            Err(error)
                if error
                    .status()
                    .is_some_and(|status| matches!(status.as_u16(), 403 | 409)) =>
            {
                self.pair_identity(&transport, &identity)
                    .await?;
                let request = self
                    .access_token_request(&transport, &identity)
                    .await?;
                transport
                    .issue_local_token(&request)
                    .await
                    .map_err(map_transport_error)?
                    .into_inner()
            },
            Err(error) => return Err(map_transport_error(error)),
        };
        let token = AccessToken {
            access_token: SecretString::from(response.access_token),
            expires_at: response.expires_at,
        };
        *cached = Some(token.clone());
        Ok(token)
    }

    async fn access_token_request(
        &self,
        transport: &transport::Client,
        identity: &crate::ClientIdentity,
    ) -> AgentClientResult<transport::types::LocalTokenRequest> {
        let challenge = transport
            .issue_local_challenge(&transport::types::LocalChallengeRequest {
                client_id: Some(identity.client_id.clone()),
                purpose: transport::types::LocalChallengePurpose::Token,
            })
            .await
            .map_err(map_transport_error)?
            .into_inner();
        let signature = sign_challenge(identity, "token", &challenge.challenge)?;
        Ok(transport::types::LocalTokenRequest {
            attestation: Some(transport::types::LocalClientAttestationInput {
                challenge_id: challenge.challenge_id,
                client_id: identity.client_id.clone(),
                origin: None,
                signature,
            }),
            client_name: Some(identity.client_name.clone()),
            requested_scopes: None,
        })
    }

    async fn pair_identity(
        &self,
        transport: &transport::Client,
        identity: &crate::ClientIdentity,
    ) -> AgentClientResult<()> {
        let grant = self.pairing_grant(transport).await?;
        if grant.expires_at <= Utc::now() {
            return Err(AgentClientError::Rejected);
        }
        let challenge = transport
            .issue_local_challenge(&transport::types::LocalChallengeRequest {
                client_id: Some(identity.client_id.clone()),
                purpose: transport::types::LocalChallengePurpose::Pairing,
            })
            .await
            .map_err(map_transport_error)?
            .into_inner();
        let signature = sign_challenge(identity, "pairing", &challenge.challenge)?;
        transport
            .complete_local_pairing(&transport::types::LocalPairingCompleteRequest {
                attestation: transport::types::LocalClientAttestationInput {
                    challenge_id: challenge.challenge_id,
                    client_id: identity.client_id.clone(),
                    origin: None,
                    signature,
                },
                client_id: identity.client_id.clone(),
                client_name: Some(identity.client_name.clone()),
                origin: None,
                pairing_secret: grant.secret.expose_secret().to_owned(),
                public_key_pem: identity.public_key_pem().to_owned(),
            })
            .await
            .map_err(map_transport_error)?;
        Ok(())
    }

    async fn pairing_grant(
        &self,
        transport: &transport::Client,
    ) -> AgentClientResult<PairingGrant> {
        match self.pairing_mode {
            PairingMode::Loopback => {
                let response = transport
                    .start_local_pairing()
                    .await
                    .map_err(map_transport_error)?
                    .into_inner();
                Ok(PairingGrant {
                    secret: SecretString::from(response.pairing_secret),
                    expires_at: response.expires_at,
                })
            },
            PairingMode::Native => {
                #[cfg(windows)]
                {
                    crate::pairing::native_pairing_grant().await
                }
                #[cfg(not(windows))]
                {
                    let _ = transport;
                    Err(AgentClientError::IdentityRequired)
                }
            },
        }
    }
}

async fn load_identity(
    store: Arc<dyn IdentityStore>,
) -> AgentClientResult<Option<crate::ClientIdentity>> {
    tokio::task::spawn_blocking(move || store.load())
        .await
        .map_err(AgentClientError::invalid_response)?
}

async fn store_identity(
    store: Arc<dyn IdentityStore>,
    identity: crate::ClientIdentity,
) -> AgentClientResult<()> {
    tokio::task::spawn_blocking(move || store.store(&identity))
        .await
        .map_err(AgentClientError::invalid_response)?
}

fn sign_challenge(
    identity: &crate::ClientIdentity,
    purpose: &str,
    challenge: &str,
) -> AgentClientResult<String> {
    let signing_key = ed25519_dalek::SigningKey::from_pkcs8_pem(
        identity
            .private_key_pem()
            .expose_secret(),
    )
    .map_err(AgentClientError::invalid_response)?;
    let message = format!("inari.local-trust.v1:{purpose}:{challenge}");
    Ok(URL_SAFE_NO_PAD.encode(
        signing_key
            .sign(message.as_bytes())
            .to_bytes(),
    ))
}

fn generated_transport_base(endpoint: &Url) -> &str {
    endpoint.as_str().trim_end_matches('/')
}

fn map_transport_error<E>(error: progenitor_client::Error<E>) -> AgentClientError
where
    E: std::fmt::Debug + Send + Sync + 'static,
{
    match error {
        progenitor_client::Error::CommunicationError(error) => AgentClientError::Unavailable(error),
        progenitor_client::Error::ErrorResponse(response)
            if matches!(response.status().as_u16(), 401 | 403 | 409) =>
        {
            AgentClientError::Rejected
        },
        progenitor_client::Error::UnexpectedResponse(response)
            if matches!(response.status().as_u16(), 401 | 403 | 409) =>
        {
            AgentClientError::Rejected
        },
        error => AgentClientError::invalid_response(error),
    }
}

#[derive(Clone)]
struct AccessToken {
    access_token: SecretString,
    expires_at: DateTime<Utc>,
}

#[cfg(test)]
mod identity_cache_tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;

    /// An identity store that counts reads and can be made to fail, standing in
    /// for a credential vault the user has not allowed access to.
    struct CountingStore {
        reads: Arc<AtomicUsize>,
        fails: bool,
    }

    impl IdentityStore for CountingStore {
        fn load(&self) -> AgentClientResult<Option<ClientIdentity>> {
            self.reads
                .fetch_add(1, Ordering::SeqCst);
            if self.fails { Err(AgentClientError::MalformedIdentity) } else { Ok(None) }
        }

        fn store(&self, _: &ClientIdentity) -> AgentClientResult<()> {
            Ok(())
        }
    }

    fn client(reads: Arc<AtomicUsize>, fails: bool) -> AgentClient {
        AgentClient::new(AgentClientOptions::default(), CountingStore { reads, fails })
            .expect("client builds")
    }

    /// The reconnect loop calls this on every attempt. On macOS each read of
    /// the vault can raise a system prompt, so reading once is the difference
    /// between one prompt and an endless run of them.
    #[tokio::test]
    async fn a_failed_credential_read_is_not_repeated_on_its_own() {
        let reads = Arc::new(AtomicUsize::new(0));
        let client = client(reads.clone(), true);

        for _ in 0..10 {
            assert!(client.identity().await.is_err());
        }

        assert_eq!(reads.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn a_locked_identity_is_reported_as_stopped_rather_than_still_trying() {
        let client = client(Arc::new(AtomicUsize::new(0)), true);

        assert!(matches!(client.identity().await, Err(AgentClientError::MalformedIdentity)));
        assert!(matches!(client.identity().await, Err(AgentClientError::IdentityLocked(_))));
    }

    #[tokio::test]
    async fn an_operator_retry_reads_the_credential_store_again() {
        let reads = Arc::new(AtomicUsize::new(0));
        let client = client(reads.clone(), true);

        assert!(client.identity().await.is_err());
        client.forget_identity().await;
        assert!(client.identity().await.is_err());

        assert_eq!(reads.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn a_successful_identity_is_resolved_once_and_then_reused() {
        let reads = Arc::new(AtomicUsize::new(0));
        let client = client(reads.clone(), false);

        let first = client
            .identity()
            .await
            .expect("identity resolves");
        let second = client
            .identity()
            .await
            .expect("identity resolves");

        assert_eq!(reads.load(Ordering::SeqCst), 1);
        assert_eq!(first.client_id, second.client_id);
    }
}
