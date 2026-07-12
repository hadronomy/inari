use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use base64::Engine;
use openidconnect::core::{CoreAuthenticationFlow, CoreClient, CoreProviderMetadata};
use openidconnect::reqwest;
use openidconnect::{
    AccessTokenHash, AuthorizationCode, ClientId, ClientSecret, CsrfToken, IssuerUrl, Nonce,
    OAuth2TokenResponse, PkceCodeChallenge, PkceCodeVerifier, RedirectUrl, Scope, TokenUrl,
};
use secrecy::{ExposeSecret, SecretString};
use serde_json::Value;
use url::Url;

use super::{AccessRole, ActorId, SessionIdentity};
use crate::config::OidcConfig;
use crate::{AppError, AppResult};

#[derive(Clone)]
pub struct IdentityService {
    inner: Arc<IdentityServiceInner>,
}

struct IdentityServiceInner {
    metadata: CoreProviderMetadata,
    client_id: ClientId,
    client_secret: Option<SecretString>,
    redirect_url: RedirectUrl,
    token_url: TokenUrl,
    scopes: Vec<Scope>,
    role_claim: String,
    role_mapping: BTreeMap<String, AccessRole>,
    http_client: reqwest::Client,
}

impl std::fmt::Debug for IdentityService {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("IdentityService")
            .field("issuer", self.inner.metadata.issuer())
            .field("client_id", &self.inner.client_id)
            .field("redirect_url", &self.inner.redirect_url)
            .finish_non_exhaustive()
    }
}

#[derive(Debug)]
pub struct LoginChallenge {
    pub authorize_url: Url,
    pub pending: PendingLogin,
}

#[derive(Clone, serde::Serialize, serde::Deserialize)]
pub struct PendingLogin {
    pub state: String,
    pub nonce: String,
    pub pkce_verifier: String,
    pub return_to: String,
}

impl std::fmt::Debug for PendingLogin {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PendingLogin")
            .field("state", &"<redacted>")
            .field("nonce", &"<redacted>")
            .field("pkce_verifier", &"<redacted>")
            .field("return_to", &self.return_to)
            .finish()
    }
}

impl IdentityService {
    pub async fn discover(
        config: &OidcConfig,
        public_url: &Url,
        client_secret: Option<SecretString>,
    ) -> AppResult<Self> {
        let issuer = config
            .issuer_url
            .clone()
            .ok_or_else(|| {
                AppError::internal("oidc_configuration", "OIDC issuer URL is not configured.")
            })?;
        let issuer = IssuerUrl::new(issuer.to_string()).map_err(|source| {
            AppError::internal("oidc_configuration", "OIDC issuer URL is invalid.")
                .with_source(source)
        })?;
        let http_client = reqwest::ClientBuilder::new()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|source| {
                AppError::internal("oidc_http_client", "OIDC HTTP client could not be built.")
                    .with_source(source)
            })?;
        let metadata = CoreProviderMetadata::discover_async(issuer, &http_client)
            .await
            .map_err(|source| {
                AppError::internal("oidc_discovery", "OIDC provider discovery failed.")
                    .with_source(source)
            })?;
        let redirect_url = public_url
            .join("auth/callback")
            .map_err(|source| {
                AppError::internal("oidc_configuration", "OIDC callback URL could not be formed.")
                    .with_source(source)
            })?;
        let redirect_url = RedirectUrl::new(redirect_url.to_string()).map_err(|source| {
            AppError::internal("oidc_configuration", "OIDC callback URL is invalid.")
                .with_source(source)
        })?;
        let token_url = metadata
            .token_endpoint()
            .cloned()
            .ok_or_else(|| {
                AppError::internal(
                    "oidc_discovery",
                    "OIDC provider metadata does not advertise a token endpoint.",
                )
            })?;
        Ok(Self {
            inner: Arc::new(IdentityServiceInner {
                metadata,
                client_id: ClientId::new(config.client_id.clone()),
                client_secret,
                redirect_url,
                token_url,
                scopes: config
                    .scopes
                    .iter()
                    .cloned()
                    .map(Scope::new)
                    .collect(),
                role_claim: config.role_claim.clone(),
                role_mapping: config.role_mapping.clone(),
                http_client,
            }),
        })
    }

    #[must_use]
    pub fn begin_login(&self, return_to: String) -> LoginChallenge {
        let client = self.client();
        let (challenge, verifier) = PkceCodeChallenge::new_random_sha256();
        let mut authorization = client
            .authorize_url(
                CoreAuthenticationFlow::AuthorizationCode,
                CsrfToken::new_random,
                Nonce::new_random,
            )
            .set_pkce_challenge(challenge);
        for scope in &self.inner.scopes {
            authorization = authorization.add_scope(scope.clone());
        }
        let (authorize_url, state, nonce) = authorization.url();
        LoginChallenge {
            authorize_url,
            pending: PendingLogin {
                state: state.secret().clone(),
                nonce: nonce.secret().clone(),
                pkce_verifier: verifier.secret().clone(),
                return_to,
            },
        }
    }

    pub async fn complete_login(
        &self,
        code: String,
        pending: &PendingLogin,
    ) -> AppResult<SessionIdentity> {
        let client = self.client();
        let response = client
            .exchange_code(AuthorizationCode::new(code))
            .set_pkce_verifier(PkceCodeVerifier::new(pending.pkce_verifier.clone()))
            .request_async(&self.inner.http_client)
            .await
            .map_err(|source| {
                AppError::unauthorized(format!(
                    "OIDC authorization code was not accepted: {source}"
                ))
            })?;
        let id_token = response
            .extra_fields()
            .id_token()
            .ok_or_else(|| AppError::unauthorized("OIDC provider did not return an ID token."))?;
        let verifier = client.id_token_verifier();
        let nonce = Nonce::new(pending.nonce.clone());
        let claims = id_token
            .claims(&verifier, &nonce)
            .map_err(|source| {
                AppError::unauthorized(format!("OIDC ID token validation failed: {source}"))
            })?;
        if let Some(expected_hash) = claims.access_token_hash() {
            let actual_hash = AccessTokenHash::from_token(
                response.access_token(),
                id_token
                    .signing_alg()
                    .map_err(|source| {
                        AppError::unauthorized(format!(
                            "OIDC signing algorithm is invalid: {source}"
                        ))
                    })?,
                id_token
                    .signing_key(&verifier)
                    .map_err(|source| {
                        AppError::unauthorized(format!("OIDC signing key is invalid: {source}"))
                    })?,
            )
            .map_err(|source| {
                AppError::unauthorized(format!("OIDC access-token hash is invalid: {source}"))
            })?;
            if actual_hash != *expected_hash {
                return Err(AppError::unauthorized("OIDC access-token hash did not match."));
            }
        }
        let raw_claims = jwt_claims(&id_token.to_string())?;
        let roles = mapped_roles(raw_claims.get(&self.inner.role_claim), &self.inner.role_mapping);
        if roles.is_empty() {
            return Err(AppError::forbidden(
                "The authenticated identity has no Inari role assignment.",
            ));
        }
        Ok(SessionIdentity {
            actor_id: ActorId::from_oidc_subject(claims.subject().as_str()),
            display_name: None,
            email: claims
                .email()
                .map(|email| email.as_str().to_owned()),
            roles,
            expires_at: claims.expiration(),
        })
    }

    fn client(
        &self,
    ) -> CoreClient<
        openidconnect::EndpointSet,
        openidconnect::EndpointNotSet,
        openidconnect::EndpointNotSet,
        openidconnect::EndpointNotSet,
        openidconnect::EndpointSet,
        openidconnect::EndpointMaybeSet,
    > {
        CoreClient::from_provider_metadata(
            self.inner.metadata.clone(),
            self.inner.client_id.clone(),
            self.inner
                .client_secret
                .as_ref()
                .map(|secret| ClientSecret::new(secret.expose_secret().to_owned())),
        )
        .set_token_uri(self.inner.token_url.clone())
        .set_redirect_uri(self.inner.redirect_url.clone())
    }
}

fn jwt_claims(token: &str) -> AppResult<serde_json::Map<String, Value>> {
    let payload = token.split('.').nth(1).ok_or_else(|| {
        AppError::unauthorized("OIDC ID token does not contain a claims payload.")
    })?;
    let decoded = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(payload)
        .map_err(|source| {
            AppError::unauthorized(format!("OIDC claims payload is invalid: {source}"))
        })?;
    serde_json::from_slice(&decoded).map_err(|source| {
        AppError::unauthorized(format!("OIDC claims payload is invalid: {source}"))
    })
}

fn mapped_roles(
    claim: Option<&Value>,
    mappings: &BTreeMap<String, AccessRole>,
) -> BTreeSet<AccessRole> {
    let values: Vec<&str> = match claim {
        Some(Value::String(value)) => vec![value],
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(Value::as_str)
            .collect(),
        Some(Value::Object(values)) => values
            .keys()
            .map(String::as_str)
            .collect(),
        _ => Vec::new(),
    };
    values
        .into_iter()
        .filter_map(|value| mappings.get(value).copied())
        .collect()
}
