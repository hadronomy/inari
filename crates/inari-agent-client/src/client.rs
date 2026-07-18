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
    identity::{IdentityStore, create_identity},
    pairing::PairingGrant,
    transport,
};

#[derive(Clone, Debug)]
pub struct AgentClientOptions {
    pub endpoint: Url,
    pub pairing_mode: PairingMode,
    pub request_timeout: Duration,
}

impl Default for AgentClientOptions {
    fn default() -> Self {
        Self {
            endpoint: Url::parse("http://127.0.0.1:8765/")
                .expect("the built-in local endpoint is valid"),
            pairing_mode: PairingMode::default(),
            request_timeout: Duration::from_secs(10),
        }
    }
}

pub struct AgentClient {
    endpoint: Url,
    http: reqwest::Client,
    identity: Arc<dyn IdentityStore>,
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
        Ok(transport::Client::new_with_client(self.endpoint.as_str(), http))
    }

    async fn access_token(&self) -> AgentClientResult<AccessToken> {
        let mut cached = self.token.lock().await;
        if let Some(token) = cached.as_ref()
            && token.expires_at > Utc::now() + ChronoDuration::seconds(30)
        {
            return Ok(token.clone());
        }

        let identity = match load_identity(self.identity.clone()).await? {
            Some(identity) => identity,
            None => {
                let identity = create_identity()?;
                store_identity(self.identity.clone(), identity.clone()).await?;
                identity
            },
        };
        let transport =
            transport::Client::new_with_client(self.endpoint.as_str(), self.http.clone());
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

fn map_transport_error(error: impl std::error::Error + Send + Sync + 'static) -> AgentClientError {
    AgentClientError::invalid_response(error)
}

#[derive(Clone)]
struct AccessToken {
    access_token: SecretString,
    expires_at: DateTime<Utc>,
}
