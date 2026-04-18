from __future__ import annotations

from base64 import urlsafe_b64encode
from hashlib import sha256
from pathlib import Path

from datetime import UTC, datetime
from urllib.parse import quote

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from .models import AgentIdentity


class AgentIdentityService:
    def __init__(
        self, *, identity_path: Path, certificate_path: Path | None = None
    ) -> None:
        self.identity_path = identity_path
        self.certificate_path = certificate_path
        self._cached_identity: AgentIdentity | None = None

    def get_or_create_identity(self) -> AgentIdentity:
        if self._cached_identity is not None:
            return self._cached_identity
        private_key = self._load_or_create_private_key()
        self._cached_identity = self._build_identity(private_key)
        return self._cached_identity

    def build_csr_pem(
        self,
        *,
        common_name: str | None = None,
        uri_sans: tuple[str, ...] = (),
    ) -> str:
        identity = self.get_or_create_identity()
        private_key = self._load_or_create_private_key()
        subject_common_name = common_name or identity.agent_id
        builder = x509.CertificateSigningRequestBuilder().subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, subject_common_name),
                ]
            )
        )
        requested_uri_sans = uri_sans or (self.default_uri_san(identity.agent_id),)
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(value) for value in requested_uri_sans]
            ),
            critical=False,
        )
        csr = builder.sign(private_key, algorithm=None)
        return csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")

    @staticmethod
    def default_uri_san(agent_id: str) -> str:
        return f"urn:inari:{quote(agent_id, safe='')}"

    def _load_or_create_private_key(self) -> Ed25519PrivateKey:
        if self.identity_path.exists():
            private_key = serialization.load_pem_private_key(
                self.identity_path.read_bytes(),
                password=None,
            )
            assert isinstance(private_key, Ed25519PrivateKey)
            return private_key
        private_key = Ed25519PrivateKey.generate()
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)
        self.identity_path.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        return private_key

    def _build_identity(self, private_key: Ed25519PrivateKey) -> AgentIdentity:
        public_key = private_key.public_key()
        raw_public = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        digest = sha256(raw_public).hexdigest()
        key_id = f"kid_{digest[:12]}"
        agent_id = f"agt_{digest[:24]}"
        certificate_pem = None
        if self.certificate_path is not None and self.certificate_path.exists():
            certificate_pem = self.certificate_path.read_text(encoding="utf-8")
        return AgentIdentity(
            agent_id=agent_id,
            key_id=key_id,
            algorithm="Ed25519",
            public_jwk={
                "kty": "OKP",
                "crv": "Ed25519",
                "alg": "EdDSA",
                "use": "sig",
                "kid": key_id,
                "x": _base64url(raw_public),
            },
            created_at=datetime.fromtimestamp(
                self.identity_path.stat().st_mtime, tz=UTC
            ),
            certificate_pem=certificate_pem,
        )


def _base64url(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
