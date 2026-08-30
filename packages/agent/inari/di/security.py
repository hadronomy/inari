from __future__ import annotations

import sys

from dishka import Provider, Scope, provide

from ..client_trust import (
    AccessTokenSigner,
    AccessTokenVerifier,
    ClientTrustService,
    ClientTrustSigningKeyStore,
    DPoPProofVerifier,
    PairingAssertionVerifier,
    RenewalDPoPProofVerifier,
    SqliteClientTrustStore,
    SystemTrustClock,
)
from ..config import AgentSettings
from ..local_api.client_trust_authorizer import BrowserClientTrustAuthorizer
from ..local_api.header_authorization import ClientTrustAuthorizer
from ..gateway.enrollment.auth import (
    UpstreamAuthProvider,
    build_upstream_auth_provider,
)
from ..security.auth import AuthorizationService
from ..security.certificates.crypto import ManagedCertificateCryptoService
from ..security.certificates.providers import (
    ClientCertificateProvider,
    build_certificate_provider,
)
from ..security.certificates.store import CertificateLifecycleService
from ..security.identity import AgentIdentityService
from ..security.local_trust import LocalTrustStore, StandaloneTrustService
from ..security.policies import SecurityPolicyService
from ..security.models import GatewayMode
from ..security.secrets import FileSecretStore, KeyringSecretStore, ProtectedSecretStore
from ..security.tls import TlsContextFactory
from ..runtime.store import RuntimeStore
from ..security.tokens import TokenService
from ..security.windows_secrets import WindowsMachineSecretStore


class SecurityProvider(Provider):
    scope = Scope.APP

    @provide
    def identity_service(self, settings: AgentSettings) -> AgentIdentityService:
        security_state_dir = settings.resolved_security_state_dir
        identity_path = security_state_dir / "agent-identity.pem"
        certificate_path = security_state_dir / "upstream-client-cert.pem"
        return AgentIdentityService(
            identity_path=identity_path,
            certificate_path=certificate_path,
        )

    @provide
    def certificate_lifecycle_service(
        self,
        settings: AgentSettings,
    ) -> CertificateLifecycleService:
        security_state_dir = settings.resolved_security_state_dir
        identity_path = security_state_dir / "agent-identity.pem"
        certificate_path = security_state_dir / "upstream-client-cert.pem"
        ca_path = security_state_dir / "upstream-ca.pem"
        return CertificateLifecycleService(
            certificate_path=certificate_path,
            private_key_path=identity_path,
            ca_path=ca_path,
        )

    @provide
    def secret_store(self, settings: AgentSettings) -> ProtectedSecretStore:
        security_state_dir = settings.resolved_security_state_dir
        if sys.platform == "win32" and settings.path_profile == "production":
            primary = WindowsMachineSecretStore(
                security_state_dir / "service-secrets.dpapi"
            )
        else:
            primary = KeyringSecretStore(
                service_name=settings.secret_store_service_name,
            )
        fallback = (
            FileSecretStore(security_state_dir / "secrets.json")
            if settings.gateway_mode is GatewayMode.STANDALONE
            and settings.path_profile != "production"
            else None
        )
        return ProtectedSecretStore(
            primary=primary,
            fallback=fallback,
        )

    @provide
    def local_trust_store(self, secret_store: ProtectedSecretStore) -> LocalTrustStore:
        return LocalTrustStore(secret_store)

    @provide
    def client_trust_store(self, store: RuntimeStore) -> SqliteClientTrustStore:
        return SqliteClientTrustStore(store.database_path)

    @provide
    def client_trust_signing_keys(
        self, secret_store: ProtectedSecretStore
    ) -> ClientTrustSigningKeyStore:
        return ClientTrustSigningKeyStore(secret_store)

    @provide
    def client_trust_service(
        self,
        settings: AgentSettings,
        store: SqliteClientTrustStore,
        signing_keys: ClientTrustSigningKeyStore,
        identity_service: AgentIdentityService,
    ) -> ClientTrustService:
        identity = identity_service.get_or_create_identity()
        signing_key = signing_keys.get_or_create()
        verification_key = {
            name: value for name, value in signing_key.items() if name != "d"
        }
        token_signer = AccessTokenSigner(
            signing_key=signing_key,
            issuer=identity.agent_id,
            audience=settings.token_audience,
        )
        return ClientTrustService(
            store=store,
            assertion_verifier=PairingAssertionVerifier(
                verification_keys={},
                agent_id=identity.agent_id,
            ),
            access_token_verifier=AccessTokenVerifier(
                verification_key=verification_key,
                issuer=identity.agent_id,
                audience=settings.token_audience,
            ),
            dpop_verifier=DPoPProofVerifier(),
            renewal_dpop_verifier=RenewalDPoPProofVerifier(),
            access_token_issuer=token_signer,
            clock=SystemTrustClock(),
        )

    @provide
    def client_trust_authorizer(
        self,
        settings: AgentSettings,
        trust: ClientTrustService,
        identity_service: AgentIdentityService,
    ) -> ClientTrustAuthorizer:
        identity = identity_service.get_or_create_identity()
        return BrowserClientTrustAuthorizer(
            trust=trust,
            agent_id=identity.agent_id,
            audience=settings.token_audience,
        )

    @provide
    def standalone_trust_service(
        self,
        settings: AgentSettings,
        local_trust_store: LocalTrustStore,
    ) -> StandaloneTrustService:
        return StandaloneTrustService(settings=settings, store=local_trust_store)

    security_policy_service = provide(SecurityPolicyService)

    @provide
    def tls_context_factory(
        self,
        settings: AgentSettings,
        certificate_lifecycle_service: CertificateLifecycleService,
    ) -> TlsContextFactory:
        return TlsContextFactory(
            settings,
            certificate_service=certificate_lifecycle_service,
        )

    @provide
    def upstream_auth_provider(
        self,
        settings: AgentSettings,
    ) -> UpstreamAuthProvider:
        return build_upstream_auth_provider(settings)

    @provide
    def certificate_provider(
        self,
        settings: AgentSettings,
        certificate_lifecycle_service: CertificateLifecycleService,
    ) -> ClientCertificateProvider:
        return build_certificate_provider(
            settings,
            certificate_service=certificate_lifecycle_service,
        )

    @provide
    def certificate_crypto_service(
        self,
        identity_service: AgentIdentityService,
    ) -> ManagedCertificateCryptoService:
        return ManagedCertificateCryptoService(identity_service=identity_service)

    @provide
    def token_service(
        self,
        settings: AgentSettings,
        secret_store: ProtectedSecretStore,
        identity_service: AgentIdentityService,
    ) -> TokenService:
        return TokenService(
            secret_store=secret_store,
            identity_service=identity_service,
            token_ttl_seconds=settings.local_token_ttl_seconds,
            token_audience=settings.token_audience,
            token_issuer=settings.token_issuer,
        )

    @provide
    def authorization_service(
        self,
        token_service: TokenService,
        security_policy_service: SecurityPolicyService,
        standalone_trust_service: StandaloneTrustService,
    ) -> AuthorizationService:
        return AuthorizationService(
            token_service=token_service,
            policy_service=security_policy_service,
            standalone_trust_service=standalone_trust_service,
        )
