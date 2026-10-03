use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use base64::Engine;
use chrono::{DateTime, Utc};
use inari_gateway::protocol::OrganizationId;
use jsonwebtoken::jwk::JwkSet;
use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header};
use openidconnect::core::{CoreAuthenticationFlow, CoreClient, CoreProviderMetadata};
use openidconnect::reqwest;
use openidconnect::{
    AccessTokenHash, AuthorizationCode, ClientId, ClientSecret, CsrfToken, Nonce,
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
    workload_audience: String,
    workload_jwks: JwkSet,
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkloadIdentity {
    pub actor_id: ActorId,
    pub database: String,
    pub company_id: String,
    pub organization_id: OrganizationId,
    pub scopes: BTreeSet<String>,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, serde::Deserialize)]
struct WorkloadClaims {
    sub: String,
    exp: i64,
    #[serde(rename = "aud")]
    _aud: serde_json::Value,
    #[serde(rename = "iss")]
    _iss: String,
    inari_database: String,
    inari_company_id: String,
    inari_organization_id: OrganizationId,
    scope: WorkloadScopeClaim,
}

#[derive(Debug, serde::Deserialize)]
#[serde(untagged)]
enum WorkloadScopeClaim {
    Text(String),
    Values(Vec<String>),
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
        let workload_jwks = serde_json::to_value(metadata.jwks())
            .and_then(serde_json::from_value::<JwkSet>)
            .map_err(|source| {
                AppError::internal(
                    "oidc_discovery",
                    "OIDC provider signing keys could not be loaded.",
                )
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
                workload_audience: config.workload_audience.clone(),
                workload_jwks,
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

    pub fn authenticate_workload(&self, token: &str) -> AppResult<WorkloadIdentity> {
        authenticate_workload_token(
            token,
            &self.inner.workload_jwks,
            self.inner.workload_audience.as_str(),
            self.inner.metadata.issuer().as_str(),
        )
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

fn authenticate_workload_token(
    token: &str,
    workload_jwks: &JwkSet,
    workload_audience: &str,
    issuer: &str,
) -> AppResult<WorkloadIdentity> {
    let header = decode_header(token)
        .map_err(|_| AppError::unauthorized("OIDC access token header is invalid."))?;
    if !matches!(header.alg, Algorithm::RS256 | Algorithm::ES256 | Algorithm::EdDSA) {
        return Err(AppError::unauthorized(
            "OIDC access token uses an unsupported signing algorithm.",
        ));
    }
    let key_id = header
        .kid
        .as_deref()
        .ok_or_else(|| AppError::unauthorized("OIDC access token kid is required."))?;
    let jwk = workload_jwks
        .find(key_id)
        .ok_or_else(|| AppError::unauthorized("OIDC access token kid is unknown."))?;
    let decoding_key = DecodingKey::from_jwk(jwk)
        .map_err(|_| AppError::unauthorized("OIDC access token key is invalid."))?;
    let mut validation = Validation::new(header.alg);
    validation.set_audience(&[workload_audience]);
    validation.set_issuer(&[issuer]);
    validation.set_required_spec_claims(&["exp", "sub", "aud", "iss"]);
    validation.leeway = 30;
    let claims = decode::<WorkloadClaims>(token, &decoding_key, &validation)
        .map_err(|_| AppError::unauthorized("OIDC access token is invalid."))?
        .claims;
    let expires_at = DateTime::from_timestamp(claims.exp, 0)
        .ok_or_else(|| AppError::unauthorized("OIDC access token expiry is invalid."))?;
    let scopes = match claims.scope {
        WorkloadScopeClaim::Text(value) => value
            .split_ascii_whitespace()
            .map(str::to_owned)
            .collect(),
        WorkloadScopeClaim::Values(values) => values.into_iter().collect(),
    };
    Ok(WorkloadIdentity {
        actor_id: ActorId::from_oidc_subject(&claims.sub),
        database: claims.inari_database,
        company_id: claims.inari_company_id,
        organization_id: claims.inari_organization_id,
        scopes,
        expires_at,
    })
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

#[cfg(test)]
mod tests {
    use base64::Engine;
    use ed25519_dalek::SigningKey;
    use ed25519_dalek::pkcs8::EncodePrivateKey;
    use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
    use rand_core::OsRng;
    use serde_json::json;

    use super::{ActorId, authenticate_workload_token};

    #[test]
    fn workload_token_is_bound_to_audience_and_organization_scope() {
        let signing_key = SigningKey::generate(&mut OsRng);
        let private_key = signing_key
            .to_pkcs8_der()
            .expect("test key should encode");
        let public_key = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(signing_key.verifying_key().to_bytes());
        let jwks = serde_json::from_value(json!({
            "keys": [{
                "kty": "OKP",
                "crv": "Ed25519",
                "x": public_key,
                "kid": "workload-key-1",
                "alg": "EdDSA",
                "use": "sig"
            }]
        }))
        .expect("test JWKS should parse");
        let mut header = Header::new(Algorithm::EdDSA);
        header.kid = Some("workload-key-1".into());
        let token = encode(
            &header,
            &json!({
                "sub": "odoo-production",
                "exp": chrono::Utc::now().timestamp() + 300,
                "aud": "urn:inari:managed-workload",
                "iss": "https://identity.example.com",
                "inari_database": "production",
                "inari_company_id": "7",
                "inari_organization_id": "org_example",
                "scope": "managed_work:write managed_work:read"
            }),
            &EncodingKey::from_ed_der(private_key.as_bytes()),
        )
        .expect("test token should encode");

        let identity = authenticate_workload_token(
            &token,
            &jwks,
            "urn:inari:managed-workload",
            "https://identity.example.com",
        )
        .expect("workload token should authenticate");

        assert_eq!(identity.actor_id, ActorId::from_oidc_subject("odoo-production"));
        assert_eq!(identity.database, "production");
        assert_eq!(identity.company_id, "7");
        assert_eq!(identity.organization_id.as_str(), "org_example");
        assert!(
            identity
                .scopes
                .contains("managed_work:write")
        );
        assert!(
            authenticate_workload_token(
                &token,
                &jwks,
                "urn:inari:wrong-audience",
                "https://identity.example.com",
            )
            .is_err()
        );
    }
}
