from __future__ import annotations

from dishka import Provider, Scope, provide

from ..config import AgentSettings
from ..gateway.auth_providers import (
    UpstreamAuthProvider,
    build_upstream_auth_provider,
)
from ..security.auth import AuthorizationService
from ..security.certificate_provisioners import (
    ClientCertificateProvisioner,
    build_certificate_provisioner,
)
from ..security.certificates import CertificateLifecycleService
from ..security.identity import AgentIdentityService
from ..security.policies import SecurityPolicyService
from ..security.secrets import FileSecretStore, KeyringSecretStore, ResilientSecretStore
from ..security.tls import TlsContextFactory
from ..security.tokens import TokenService


class SecurityProvider(Provider):
    scope = Scope.APP

    @provide
    def identity_service(self, settings: AgentSettings) -> AgentIdentityService:
        identity_path = settings.security_state_dir / "agent-identity.pem"
        certificate_path = settings.security_state_dir / "upstream-client-cert.pem"
        return AgentIdentityService(
            identity_path=identity_path,
            certificate_path=certificate_path,
        )

    @provide
    def certificate_lifecycle_service(
        self,
        settings: AgentSettings,
    ) -> CertificateLifecycleService:
        identity_path = settings.security_state_dir / "agent-identity.pem"
        certificate_path = settings.security_state_dir / "upstream-client-cert.pem"
        ca_path = settings.security_state_dir / "upstream-ca.pem"
        return CertificateLifecycleService(
            certificate_path=certificate_path,
            private_key_path=identity_path,
            ca_path=ca_path,
        )

    @provide
    def secret_store(self, settings: AgentSettings) -> ResilientSecretStore:
        security_state_dir = settings.security_state_dir
        return ResilientSecretStore(
            primary=KeyringSecretStore(
                service_name=settings.secret_store_service_name,
            ),
            fallback=FileSecretStore(security_state_dir / "secrets.json"),
        )

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
    def certificate_provisioner(
        self,
        settings: AgentSettings,
        identity_service: AgentIdentityService,
        certificate_lifecycle_service: CertificateLifecycleService,
    ) -> ClientCertificateProvisioner:
        return build_certificate_provisioner(
            settings,
            identity_service=identity_service,
            certificate_service=certificate_lifecycle_service,
        )

    @provide
    def token_service(
        self,
        settings: AgentSettings,
        secret_store: ResilientSecretStore,
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
    ) -> AuthorizationService:
        return AuthorizationService(
            token_service=token_service,
            policy_service=security_policy_service,
        )
